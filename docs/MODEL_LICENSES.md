# Model, Library, and Asset Notices

LastWhisper does not redistribute model weights. Users download models from their original publishers and are responsible for complying with those terms.

## Default AI Components

| Component | Purpose | Upstream license/source |
| --- | --- | --- |
| faster-whisper | Local speech transcription runtime | MIT, https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | Local optimized inference | MIT, https://github.com/OpenNMT/CTranslate2 |
| SYSTRAN faster-whisper-base conversion | Default CTranslate2 speech weights | MIT model card, https://huggingface.co/Systran/faster-whisper-base |
| OpenAI Whisper models | Original Whisper architecture and weights | MIT project license, https://github.com/openai/whisper |
| paraphrase-multilingual-MiniLM-L12-v2 | Default sentence embedding model | Apache-2.0 model card, https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 |
| sentence-transformers | Embedding runtime | Apache-2.0, https://github.com/huggingface/sentence-transformers |
| PyTorch | Tensor runtime | BSD-style, https://github.com/pytorch/pytorch |

The default Whisper conversion is pinned to `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`; the default embedding model is pinned to `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`. Changing either model or revision requires reviewing the replacement model's license, data policy, size, and compatibility.

## Optional Ollama Models

Ollama is optional and LastWhisper does not select, bundle, or redistribute an Ollama model. Licenses vary by model and may restrict commercial use or redistribution. Check the exact model card before enabling or demonstrating it.

## Major Application Libraries

- FastAPI: MIT
- SQLAlchemy: MIT
- SQLite: public domain
- React: MIT
- Vite: MIT
- Tailwind CSS: MIT
- Lucide: ISC
- Nginx Docker image: BSD-2-Clause project license, with image components under their respective licenses

## Fonts and Visual Assets

No remote font, stock image, screenshot, or third-party visual asset is bundled. The interface uses system/installed font fallbacks and Lucide icons. Demo recordings and model caches used during verification are disposable local fixtures and are excluded from Git.

## FFmpeg

FFmpeg is used only as an external system/container dependency. Its effective LGPL/GPL configuration depends on the distributor's build; consult that package's license information for redistribution.
