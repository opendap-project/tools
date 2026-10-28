# Hardware toolkit requirements

Requirements TK-001, TK-009, TK-010 and TK-011 in [cli](https://github.com/opendap-project/host/blob/main/cli/docs/requirements/README.md) apply to every toolkit repository.

| ID | Requirement | Priority | Status |
|---|---|---|---|
| TK-014 | Product-side 3D animations and interactive previews that do not need Blender may be standalone HTML documents using libraries such as three.js or anime.js. | should | Confirmed |
| TK-015 | Product presentation videos may be produced from the standalone HTML previews: captured frame by frame with Playwright and assembled with ffmpeg. | should | Confirmed |

- Video capture must be deterministic: the page's clock is stepped frame by frame (three.js clock, anime.js `seek`, a controlled `requestAnimationFrame`) instead of recording in real time, so every frame is exact regardless of host speed.
