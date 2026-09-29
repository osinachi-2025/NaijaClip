from __future__ import annotations

import os


class DeepgramClient:
    def __init__(self, api_key: str | None = None):
        from deepgram import DeepgramClient as SDKClient

        self.client = SDKClient(api_key=api_key or os.getenv("DEEPGRAM_API_KEY"))

    def transcribe(self, audio_path: str, model: str | None = None):
        with open(audio_path, "rb") as audio:
            response = self.client.listen.v1.media.transcribe_file(
                request=audio.read(),
                model=model or os.getenv("DEEPGRAM_MODEL", "nova-3"),
                smart_format=True,
                utterances=True,
                paragraphs=True,
                punctuate=True,
            )
        return response
