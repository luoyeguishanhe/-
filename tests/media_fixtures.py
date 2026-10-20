"""Small real media fixtures, encoded locally with PyAV."""

from fractions import Fraction
import math

import av
import numpy as np


def create_video(path, audio=None, audio_start=2.0, audio_rate=16000,
                 duration=4.0, mp4=False, audio_gap_after=None, gap_seconds=0):
    packets = []
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4" if mp4 else "ffv1", rate=10)
        video.width, video.height, video.pix_fmt = 64, 32, "yuv420p"
        for index in range(math.ceil(duration * 10)):
            frame = av.VideoFrame.from_ndarray(np.zeros((32, 64, 3), dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            packets.extend(video.encode(frame))
        packets.extend(video.encode(None))
        if audio is not None:
            stream = container.add_stream("aac" if mp4 else "pcm_s16le", rate=audio_rate)
            stream.layout = "mono"
            for start in range(0, len(audio), 1024):
                data = np.rint(np.clip(audio[start:start + 1024], -1, 1) * 32767).astype("int16")
                frame = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
                frame.sample_rate = audio_rate
                gap = round(gap_seconds * audio_rate) if audio_gap_after is not None and start >= audio_gap_after else 0
                frame.pts = round(audio_start * audio_rate) + start + gap
                frame.time_base = Fraction(1, audio_rate)
                packets.extend(stream.encode(frame))
            packets.extend(stream.encode(None))
        packets.sort(key=lambda packet: (packet.dts if packet.dts is not None else packet.pts) * packet.time_base)
        for packet in packets:
            container.mux(packet)
