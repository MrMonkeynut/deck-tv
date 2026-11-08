"""Splits a song into its singing and everything else, for karaoke. Runs in the vocal remover's own
Python (karaoke-env, with Demucs), started by karaoke.py:  separate.py <song file> <output folder>

Writes backing.opus (the song without the singing) and vocals.opus (just the singing, for the guide
vocal). ffmpeg reads and writes the audio, so any format ffmpeg knows will do.
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model


def write(samples, rate, file):
    tmp = file.with_suffix(".part.opus")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "2", "-i", "-",
                    "-c:a", "libopus", "-b:a", "160k", str(tmp)],
                   input=samples.t().contiguous().numpy().astype(np.float32).tobytes(), check=True)
    tmp.replace(file)


def main(song, out):
    torch.set_num_threads(4)
    model = get_model("htdemucs")  # downloaded once (about 80 MB) into ~/.cache/torch
    model.eval()
    rate = model.samplerate
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", song, "-vn", "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"],
                         capture_output=True, check=True).stdout
    wav = torch.from_numpy(np.frombuffer(pcm, dtype=np.float32).copy()).view(-1, 2).t()
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        stems = apply_model(model, ((wav - mean) / std)[None], device="cpu", split=True, overlap=0.25)[0]
    stems = stems * std + mean
    vocals = stems[model.sources.index("vocals")]
    out = Path(out)
    write(vocals, rate, out / "vocals.opus")
    write(wav - vocals, rate, out / "backing.opus")  # last: its being there means the song is ready


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
