---
name: Long URLs — deliver via file or clipboard, not chat/terminal
description: Rendered terminal/chat display wraps long URLs, so copy-paste from the chat breaks them; use clipboard or files instead
type: feedback
originSessionId: <globus-endpoint-id>
---
When handing the user a long URL (e.g., Neuroglancer viewer links with encoded state ~1000+ chars), NEVER present it inline in a chat message expecting them to copy it. The rendered VSC/terminal display inserts real newlines mid-URL, so pasting into a browser or Excel splits it.

**Why:** happened twice in the Cairns_Shank2 NG-links session (2026-09-30) — user could not paste working URLs from the chat display; each URL fragment landed in a different Excel row / broke the browser paste.

**How to apply — pick one:**
- Put it on the Windows clipboard: `Get-Content <file> -Raw | Set-Clipboard` (PowerShell) or pipe the string through `clip`.
- Write it to a `.txt` file the user opens in Notepad and copies from (Notepad's word-wrap is visual-only).
- Write it into an `.xlsx` cell as a hyperlink (the user copies the cell → cell-to-cell paste preserves it whole).

Applies to any string longer than ~120 chars that must be pasted as a single line: encoded URLs, tokens, one-line JSON, base64 blobs, etc.
