---
name: feedback-ignore-pff-samples
description: User wants all PFF samples left out of every future analysis; tetraspec bead images hidden from the gallery
metadata:
  node_type: memory
  type: feedback
  originSessionId: <globus-endpoint-id>
  modified: 2026-10-07T14:28:29.164Z
---

On 2026-10-07 the user said: "remember ignore all PFF for future testing and delete them", and earlier, for the bead images, "Ignore the tetraspec for now. in fact, remove them from the online gallery."

**Why:** PFF samples (`260526_PFF1_*`) are not part of the antibody-efficiency testing; tetraspec are bead calibration images, not tissue.

**How to apply:** Leave any sample with `pff` or `tetraspec` in its name out of every analysis and out of the gallery. In `Desktop\ZarrBatchConverter` this is `NEVER` in `synapse_exf.py` (used by the other tools), `SKIP` in `channel_check.py`, `HIDDEN` in `make_gallery.py`. After the user confirmed ("delete all PFF everywhere", 2026-10-07) I deleted the five PFF zarrs on `X:\nginx_share\exm\2026\zarr`, their `_ng_states` files, screenshot folders, gallery thumbnails and synapse folders. The five raw `.ims` originals in `X:\nginx_share\exm\2026\AB Screening\Double Label\260526 Double Label\2026-05-26\` were left: I asked whether "everywhere" includes raw acquisitions. Do not delete raw originals without an explicit yes. See [[kuanlab-synapse-exf-analysis]].
