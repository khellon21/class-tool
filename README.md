# Class Notes

Record a class (in person or on Teams), drop in the slides, get study notes.
Transcription runs locally on your Mac; note-writing calls DeepSeek via NVIDIA's API.

## Setup

Already done: Homebrew `ffmpeg` + `whisper-cpp`, a Python 3.13 venv, and the
`ggml-small.en` Whisper model in `models/`.

The one thing left is the API key:

```bash
echo 'NVIDIA_API_KEY=nvapi-your-key-here' > .env
```

## Run

Double-click **Start Class Notes.command** in Finder. It opens a Terminal window,
starts the app, and opens your browser at http://localhost:5005.

**Leave that Terminal window open while you use the app.** Closing it, or pressing
Ctrl+C, stops the server — which is why the page goes unreachable. Double-click the
launcher again to bring it back; if it is already running it just reopens the tab.

From a terminal instead: `./run.sh`  (add `DEBUG=1` for auto-reload while editing code).

## Using it

1. The course box is pre-filled with whichever class it thinks is on now (see below).
   Pick the date and class type.
2. Attach the materials for this class — slides, handouts, readings, whiteboard photos.
   Tick *keep for every class in this course* for things like a textbook or syllabus,
   or carry a file over from an earlier day under *Carry over from an earlier day*.
3. **Start recording.** Materials upload and get parsed while the class runs, so notes
   are quick to generate afterwards.
   *In person* records your mic — put the laptop where it can hear the lecturer.
   *Online / Teams* captures shared audio: Chrome asks what to share, so pick the
   Teams tab and tick *Share tab audio*. On the Teams desktop app, share your whole
   screen and tick *Share system audio* instead.
4. Hit stop when class ends. You land on the session page, where you can still add
   materials you did not have at the start.
5. **Generate notes.** Most of the transcript is already done (see below), so this is
   mostly the time the model takes to write — around a minute for a full lecture.
6. Notes are editable; **Save edits** writes them back to disk.

## Transcribing during class

Whisper is the slow part: roughly 8x faster than realtime, so an hour lecture
would be about 8 minutes of transcription if it all happened at the end.

Instead the browser rotates its recorder every 2 minutes and uploads each finished
chunk mid-class, and the server transcribes each one as it arrives. By the time you
hit stop, everything except the last chunk is already done — so the wait drops from
about 8 minutes to about one.

The recording counter shows progress (`3 of 8 chunks transcribed`), and the session
page shows what is left before the notes can be written.

Chunks land in `segments/` as `0000.webm` + `0000.txt`, and are merged into a single
`recording.webm` for playback when you stop. Nothing is lost if a chunk fails to
upload or transcribe: the missing pieces are redone when you hit Generate, and a
chunk that is truly unreadable is skipped rather than sinking the whole lecture.

Sessions recorded before this change still work — with no `segments/` folder, the
whole file is transcribed the old way.

## Which class is on now

Every session records when you started it. Once you have recorded a class a couple
of times, the course box pre-fills with whatever matches the current weekday and
time of day — no schedule to set up. Before that it just stays empty.

## Where things live

```
classes/<Course>/
  _shared/                    used for every class in this course
  <YYYY-MM-DD>/
    segments/                 2-minute chunks, transcribed during class
      0000.webm  0000.txt
    recording.webm            the chunks merged, for playback
    transcript.txt            the stitched transcript
    materials/                the files for this day
    materials_cache.json      parsed material text, refreshed when files change
    meta.json                 when the session started
    notes.md                  the generated notes
```

Plain folders — browsable in Finder, and safe to drop in iCloud or Dropbox.
Carried-over materials are copied, not linked, so each day's folder stands alone.

## Notes structure

With materials present, the notes follow the slide/section order and fill each
heading in with what was actually said. Anything the lecture covered that the
slides did not lands in *Additional Material From Lecture*. With no materials,
the notes organize by topic instead. Either way they end with a Summary and Key Terms.

## Knobs

| Env var | Default | What it does |
|---|---|---|
| `NVIDIA_API_KEY` | — | Required. |
| `NOTES_MODEL` | `deepseek-ai/deepseek-v4-pro-0813` | Any model on NVIDIA's endpoint. |
| `WHISPER_MODEL` | `ggml-small.en.bin` | Filename in `models/`. |

A bigger Whisper model is more accurate but slower — worth it for a hard-to-hear
lecturer, overkill otherwise:

```bash
curl -L -o models/ggml-medium.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin
```

## Tests

```bash
.venv/bin/python test_pipeline.py
```

Covers material extraction and prompt building. The Whisper and API legs are
verified by actually running a class through.
