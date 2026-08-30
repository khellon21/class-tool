"""Class notes tool: record a lecture, transcribe locally, generate notes from transcript + materials."""
import os
import re
import json
import shutil
import threading
import subprocess
import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory, abort

import materials as mat

ROOT = Path(__file__).parent
CLASSES = ROOT / "classes"
WHISPER_MODEL = ROOT / "models" / os.environ.get("WHISPER_MODEL", "ggml-small.en.bin")

SHARED = "_shared"  # per-course materials folder, included in every session
WHISPER_LOCK = threading.Lock()  # one Whisper process at a time on an 8GB machine
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.environ.get("NOTES_MODEL", "deepseek-ai/deepseek-v4-pro-0813")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB, a long lecture recording


def course_dir(course):
    if not re.fullmatch(r"[\w .-]{1,64}", course) or course.startswith("_"):
        abort(400, "bad course name")
    d = (CLASSES / course).resolve()
    if not d.is_relative_to(CLASSES.resolve()):
        abort(400, "bad path")
    return d


def session_dir(course, date):
    """Resolve a session folder, refusing anything that escapes CLASSES."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        abort(400, "bad date")
    return course_dir(course) / date


def save_uploads(files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        name = Path(f.filename).name  # strip any directory component from the browser
        if name and not name.startswith("."):
            f.save(dest / name)


def list_sessions():
    out = []
    for course in sorted(p for p in CLASSES.glob("*") if p.is_dir()):
        days = (p for p in course.glob("*") if p.is_dir() and not p.name.startswith("_"))
        for day in sorted(days, reverse=True):
            out.append({
                "course": course.name,
                "date": day.name,
                "has_audio": (day / "recording.webm").exists(),
                "has_notes": (day / "notes.md").exists(),
            })
    return out


def suggest_course():
    """Guess which class is on now, learned from when past sessions were recorded.

    Scores each course by how closely its past start times match this weekday
    and time of day. Returns "" until there is history to learn from.
    """
    now = datetime.datetime.now()
    now_min = now.hour * 60 + now.minute
    scores = {}
    for course in (p for p in CLASSES.glob("*") if p.is_dir()):
        for day in course.glob("*"):
            try:
                meta = json.loads((day / "meta.json").read_text())
                started = datetime.datetime.fromisoformat(meta["started_at"])
            except (OSError, ValueError, KeyError):
                continue
            if started.weekday() != now.weekday():
                continue
            gap = abs((started.hour * 60 + started.minute) - now_min)
            if gap <= 90:  # ponytail: fixed window; make it per-course if classes cluster
                scores[course.name] = scores.get(course.name, 0) + (90 - gap)
    return max(scores, key=scores.get) if scores else ""


def course_material_index(course):
    """What this course already has available to reuse: shared plus each past day."""
    root = course_dir(course)
    past = []
    days = (p for p in root.glob("*") if p.is_dir() and not p.name.startswith("_"))
    for day in sorted(days, reverse=True):
        names = [p.name for p in mat.readable_files(day / "materials")]
        if names:
            past.append({"date": day.name, "files": names})
    return {
        "shared": [p.name for p in mat.readable_files(root / SHARED)],
        "sessions": past,
    }


# ---------- pipeline ----------

def material_dirs(session):
    """Shared course materials first, then this session's own."""
    return [session.parent / SHARED, session / "materials"]


def extract_materials(session):
    labels = {str(session.parent / SHARED): "course material: "}
    return mat.extract_cached(session / "materials_cache.json", *material_dirs(session), label=labels)


def prefetch_materials(session):
    """Parse materials in the background so they are ready when class ends."""
    threading.Thread(target=extract_materials, args=(session,), daemon=True).start()


def transcribe_file(webm, out_prefix):
    """webm -> 16kHz mono wav -> whisper.cpp -> <out_prefix>.txt

    Serialized: several segments can land at once and this box has 8GB, so
    only one Whisper process runs at a time. The done-check is inside the
    lock so a segment queued twice is only transcribed once.
    """
    out = Path(f"{out_prefix}.txt")
    with WHISPER_LOCK:
        if out.exists():
            return out.read_text()
        if not WHISPER_MODEL.exists():
            raise RuntimeError(f"Whisper model missing at {WHISPER_MODEL}. See README for the download command.")

        wav = Path(webm).with_suffix(".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(webm), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
            check=True, capture_output=True,
        )
        # -nt strips timestamps: notes want prose, not a subtitle file.
        subprocess.run(
            ["whisper-cli", "-m", str(WHISPER_MODEL), "-f", str(wav), "-nt", "-otxt", "-of", str(out_prefix)],
            check=True, capture_output=True,
        )
        wav.unlink(missing_ok=True)
        return out.read_text()


def segment_files(session):
    return sorted((session / "segments").glob("[0-9]*.webm"))


def transcribe_segment_async(session, webm):
    """Transcribe one mid-class segment in the background, so the wait at the
    end of a lecture is one segment instead of the whole hour."""
    def work():
        try:
            transcribe_file(webm, webm.with_suffix(""))
        except Exception as e:
            app.logger.warning("segment %s failed: %s", webm.name, e)
    threading.Thread(target=work, daemon=True).start()


def segment_status(session):
    segs = segment_files(session)
    done = sum(1 for s in segs if s.with_suffix(".txt").exists())
    return {"total": len(segs), "done": done, "finished": (session / "segments" / ".done").exists()}


def assemble_transcript(session):
    """Stitch the per-segment transcripts together, transcribing any that the
    background workers did not finish (or that failed) as we go."""
    segs = segment_files(session)
    if not segs:
        # A session recorded before segmenting, or an uploaded file.
        webm = session / "recording.webm"
        if not webm.exists():
            raise RuntimeError("No recording found for this session.")
        return transcribe_file(webm, session / "transcript")

    parts = []
    for seg in segs:
        try:
            parts.append(transcribe_file(seg, seg.with_suffix("")).strip())
        except Exception as e:
            app.logger.warning("segment %s unusable: %s", seg.name, e)
    text = "\n".join(p for p in parts if p)
    (session / "transcript.txt").write_text(text)
    return text


def merge_segments_audio(session):
    """Join the segments into one playable recording.webm for the audio player."""
    segs = segment_files(session)
    if not segs or (session / "recording.webm").exists():
        return
    listing = session / "segments" / "concat.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segs))
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(session / "recording.webm")],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        app.logger.warning("could not merge audio: %s", e.stderr.decode()[-300:])
    finally:
        listing.unlink(missing_ok=True)


def build_prompt(transcript, material_text):
    if material_text:
        task = (
            "Use the class materials below as the skeleton for the notes: follow their section/slide "
            "order and headings. Under each heading, fill in what the lecturer actually said in the "
            "transcript - explanations, examples, emphasis, tangents worth keeping. If the lecture "
            "covered something absent from the materials, add it in a final '## Additional Material "
            "From Lecture' section. If the materials cover something the lecture skipped, keep the "
            "heading and note it was not discussed. Items marked 'course material:' are course-wide "
            "references such as a textbook or syllabus - draw on them for context, but structure the "
            "notes around the material specific to this class."
        )
        materials_block = f"\n\n=== CLASS MATERIALS ===\n{material_text}\n"
    else:
        task = (
            "No class materials were provided, so organize the notes by the topics the lecturer "
            "actually moved through, in order, using your own descriptive headings."
        )
        materials_block = ""

    return (
        "You are turning a lecture recording into study notes.\n\n"
        f"{task}\n\n"
        "Write in markdown. Be substantive - these replace attending the class, not a one-line summary. "
        "Preserve concrete detail: definitions, formulas, worked examples, dates, names, anything flagged "
        "as exam-relevant. Drop filler, admin chatter, and repetition. The transcript is machine-generated "
        "so it has errors; silently correct obvious mistranscriptions using the materials as ground truth "
        "for technical terms and names.\n\n"
        "End with two sections: '## Summary' (a short paragraph on what this class was about) and "
        "'## Key Terms' (a bullet per term with a one-line definition).\n"
        f"{materials_block}\n"
        f"=== LECTURE TRANSCRIPT ===\n{transcript}\n"
    )


def generate_notes(session):
    from openai import OpenAI

    key = os.environ.get("NVIDIA_API_KEY")
    if not key or key == "paste-your-key-here":
        raise RuntimeError("NVIDIA_API_KEY is not set in .env. Add it and restart the app.")

    transcript = assemble_transcript(session).strip()
    if not transcript:
        raise RuntimeError("Transcript came back empty - check that the recording captured audio.")

    client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key)
    resp = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=[{"role": "user", "content": build_prompt(transcript, extract_materials(session))}],
        temperature=0.3,
        max_tokens=16000,
    )
    notes = resp.choices[0].message.content
    (session / "notes.md").write_text(notes)
    return notes


# ---------- routes ----------

@app.get("/")
def home():
    return render_template("index.html", sessions=list_sessions(), suggested=suggest_course())


@app.get("/session/<course>/<date>")
def session_page(course, date):
    d = session_dir(course, date)
    if not d.exists():
        abort(404)
    read = lambda name: (d / name).read_text() if (d / name).exists() else ""
    return render_template(
        "session.html",
        course=course, date=date,
        transcript=read("transcript.txt"), notes=read("notes.md"),
        has_audio=(d / "recording.webm").exists(),
        materials=[p.name for p in mat.readable_files(d / "materials")],
        shared=[p.name for p in mat.readable_files(d.parent / SHARED)],
    )


@app.get("/api/course/<course>/materials")
def course_materials(course):
    return jsonify(course_material_index(course))


@app.post("/api/course/<course>/shared")
def upload_shared(course):
    d = course_dir(course) / SHARED
    save_uploads(request.files.getlist("files"), d)
    return jsonify(shared=[p.name for p in mat.readable_files(d)])


@app.post("/api/session")
def create_session():
    course = (request.json.get("course") or "").strip()
    date = request.json.get("date") or datetime.date.today().isoformat()
    d = session_dir(course, date)
    (d / "materials").mkdir(parents=True, exist_ok=True)
    meta = d / "meta.json"
    if not meta.exists():  # keep the original start time if re-recording the same day
        meta.write_text(json.dumps({"started_at": datetime.datetime.now().isoformat(timespec="seconds")}))
    return jsonify(course=course, date=date, url=f"/session/{course}/{date}")


@app.post("/api/session/<course>/<date>/audio")
def upload_audio(course, date):
    d = session_dir(course, date)
    d.mkdir(parents=True, exist_ok=True)
    request.files["audio"].save(d / "recording.webm")
    return jsonify(ok=True)


@app.post("/api/session/<course>/<date>/segment")
def upload_segment(course, date):
    """One mid-class chunk. Transcribed immediately so the wait at the end is short."""
    d = session_dir(course, date)
    segs = d / "segments"
    segs.mkdir(parents=True, exist_ok=True)
    try:
        index = int(request.form["index"])
    except (KeyError, ValueError):
        abort(400, "bad segment index")

    webm = segs / f"{index:04d}.webm"
    request.files["audio"].save(webm)
    transcribe_segment_async(d, webm)
    return jsonify(segment_status(d))


@app.post("/api/session/<course>/<date>/finish")
def finish_recording(course, date):
    d = session_dir(course, date)
    segs = d / "segments"
    if segs.is_dir():
        (segs / ".done").touch()
        threading.Thread(target=merge_segments_audio, args=(d,), daemon=True).start()
    return jsonify(segment_status(d))


@app.get("/api/session/<course>/<date>/status")
def status(course, date):
    return jsonify(segment_status(session_dir(course, date)))


@app.post("/api/session/<course>/<date>/materials")
def upload_materials(course, date):
    d = session_dir(course, date)
    save_uploads(request.files.getlist("files"), d / "materials")
    prefetch_materials(d)
    return jsonify(files=[p.name for p in mat.readable_files(d / "materials")])


@app.post("/api/session/<course>/<date>/import")
def import_materials(course, date):
    """Copy materials from earlier days of this course into this session.

    Copies rather than links so each session folder stays self-contained.
    """
    d = session_dir(course, date)
    (d / "materials").mkdir(parents=True, exist_ok=True)
    copied = []
    for ref in request.json.get("files", []):
        src_date, _, name = str(ref).partition("/")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", src_date) or Path(name).name != name or not name:
            continue
        src = course_dir(course) / src_date / "materials" / name
        if src.is_file():
            shutil.copy2(src, d / "materials" / name)
            copied.append(name)
    prefetch_materials(d)
    return jsonify(copied=copied)


@app.post("/api/session/<course>/<date>/generate")
def generate(course, date):
    d = session_dir(course, date)
    try:
        return jsonify(notes=generate_notes(d))
    except subprocess.CalledProcessError as e:
        return jsonify(error=f"{Path(e.cmd[0]).name} failed: {e.stderr.decode()[-500:]}"), 500
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.post("/api/session/<course>/<date>/notes")
def save_notes(course, date):
    (session_dir(course, date) / "notes.md").write_text(request.json.get("notes", ""))
    return jsonify(ok=True)


@app.get("/api/session/<course>/<date>/audio")
def get_audio(course, date):
    return send_from_directory(session_dir(course, date), "recording.webm")


if __name__ == "__main__":
    CLASSES.mkdir(exist_ok=True)
    # Debug off by default: the Werkzeug debugger can run code, and this serves uploads.
    app.run(port=5005, debug=os.environ.get("DEBUG") == "1")
