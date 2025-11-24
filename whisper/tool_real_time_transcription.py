import queue

import numpy as np
import sounddevice as sd

from utils_whisper import get_turbo_service


SAMPLE_RATE = 16000
BLOCK_SIZE = 2048
CHANNELS = 1
SEGMENT_SECONDS = 5      # 每段 10 秒，降低延迟

audio_queue = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        print(status)
    audio_queue.put(indata.copy())


print("Listening... Ctrl+C to stop.")

buffer = np.zeros(0, dtype=np.float32)
required_samples = SEGMENT_SECONDS * SAMPLE_RATE  # 10 秒所需采样点

service = get_turbo_service()

try:
    with sd.InputStream(
        channels=CHANNELS,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        callback=audio_callback,
    ):
        # 只输出一次前缀，后续文字持续接在同一行
        print("Transcription: ", end="", flush=True)

        while True:
            block = audio_queue.get().flatten()
            buffer = np.concatenate((buffer, block))

            # 满 SEGMENT_SECONDS 才处理
            if len(buffer) >= required_samples:
                chunk = buffer[:required_samples]
                buffer = buffer[required_samples:]

                text = service.transcribe_array(
                    chunk,
                    SAMPLE_RATE,
                    task="transcribe",
                    language=None,  # 自动侦测语言
                )
                # 将新的识别结果直接接在同一行后面
                print(text, end=" ", flush=True)

except KeyboardInterrupt:
    print("\nStopping...")
