# Verification of 0.1.0

Date: 2026-10-06.

## Executed

- `python -m localmodel doctor`: passed.
- `python -m localmodel plan`: 1 character image, 3 outfit images, 3 video requests,
  36 generated video seconds for a 12-second final output.
- `python -m localmodel demo`: produced an actual six-second synthetic diagnostic MP4.
- `python -m pytest -q`: **49 passed**.

Environment: Linux, Python 3.13.5, imageio-ffmpeg 0.6.0 (bundled FFmpeg 7.0.2),
NumPy 2.3.5, librosa 0.11.0, pytest 9.0.2. All test cases ran; no skip marker was used.

## Measured diagnostic output

- Video: H.264, 180x320, 30 fps, 180 decoded frames.
- Audio: AAC, duration approximately six seconds, non-silent.
- Cuts: frames 0, 61, 121, 180 after beat detection and frame quantization.
- Timeline saved as JSON with source intervals and SHA-256 media fingerprints.
- A visual-data regression test compares the rendered frame against the correct
  absolute source time and rejects restarting a clip at a transition.

During verification, FFmpeg initially chose a 25 fps output time base after concat.
The renderer now explicitly sets output frame rate/CFR and decodes the result to
check its exact frame count. The integration test covers this regression.

## Not executed or established

No real FLUX/Kling generation, cloud upload or paid API call. The REST adapter uses
injected transport responses in tests. Endpoint schemas were checked against the
provider's official documentation, not against an authenticated production run.
No confirmation of character identity preservation, hand quality or pose matching.
No Windows/PowerShell runtime test and no GPU benchmark.
A fresh online dependency installation was not verified; outbound package-index
DNS was unavailable in the execution environment. Existing listed dependencies
were used for testing.

The technical demo must not be presented as an AI-generated woman. No user-uploaded
video, private face reference, API key or model weight is part of this repository.
