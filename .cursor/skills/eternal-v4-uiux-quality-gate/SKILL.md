---
name: eternal-v4-uiux-quality-gate
description: WAJIB gate UI/UX V4 sebelum Owner preview approval, final merge review dan review release gabungan bila UI terdampak.
---

# UI/UX Quality Gate 1.0

Baca semua skill UI V4 dan gunakan kontrak
final-merge-review/cross-pr-semantic-conflict-gate
dari workflow ChatGPT.

Berlaku untuk setiap perubahan UI/UX/layout/
visual/FE V4 dan perubahan tidak langsung
yang memengaruhi UI.

Tidak berlaku untuk V1.

Tidak menggantikan unit tests, build,
CI exact HEAD, production baseline smoke,
Owner preview approval, semantic conflict gate,
final merge review atau release approval.

Sebelum preview:
- Kontrak desain disetujui.
- Implementasi dan browser QA/self-correction.
- Evidence per scope.
- Baseline smoke terpisah.

PASS hanya jika semua kriteria applicable
terbukti, tidak ada P0/P1 baru/terdampak yang
belum terselesaikan, tidak ada pelanggaran
scope/fitur/gate, dan bukti visual/interaksi
nyata tersedia.

P2/P3 yang tidak material boleh dicatat
dengan alasan dan risiko, bukan disembunyikan.

N/A harus beralasan.
NOT_VERIFIED material = HOLD.
FAIL jika terbukti melanggar.

Jangan klaim preview aman, stop sebelum
finalisasi Git bila gate HOLD/FAIL.

Owner approval tidak mengubah bukti
NOT_VERIFIED menjadi PASS.

Baseline masalah di luar scope dicatat
dan eskalasi jika material.

Pre-merge:
review diff dan evidence pada exact HEAD terbaru.

Reuse bukti valid hanya jika source/fixture/
kontrak relevan identik; buktikan mapping
uncommitted preview ke commit.

Drift material: retest affected.

Cross-PR semantic gate tetap wajib,
termasuk CSS/token/shared component dan
combined tree.

CI hijau bukan visual PASS.

Final merge review wajib HOLD bila gate
belum PASS.

Pre-release gabungan:
cek seluruh LIVE..TARGET UI delta dan
interaksi shared CSS/component,
combined target proof,
approval release terpisah.

Postdeploy:
UI_PARITY PASS hanya dengan bukti browser
Owner vs preview pada viewport sebanding
dan asset provenance.

Jika gagal STOP, ikuti rollback policy.
Tidak melakukan production write.

## Mandatory Report

PASS/FAIL/HOLD/NOT_VERIFIED/N/A sesuai bukti.
NONE untuk defect yang benar-benar dicek.

STANDARD_VERSION:
DESIGN_CONTRACT:
BASE_SHA:
TESTED_SOURCE_FINGERPRINT:
HEAD_SHA:
PREVIEW_URL:

DESIGN_TOKEN_COMPLIANCE:
SHARED_COMPONENT_REUSE:

DESKTOP_VISUAL:
LAPTOP_VISUAL:
TABLET_VISUAL:
MOBILE_VISUAL:

TEXT_CLIPPING:
UNINTENDED_OVERLAP:
UNINTENDED_OVERFLOW:

ACTION_REACHABILITY:
TABLE_SCROLL:
MODAL_INTERACTION:
FORM_INTERACTION:
ACCESSIBILITY_SCOPE:

FEATURE_PRESERVATION:
BUSINESS_RULE_PRESERVATION:
CROSS_MODULE_REGRESSION:

CONSOLE_RUNTIME:
PREVIEW_COMPONENT_PARITY:
BROWSER_EVIDENCE:

SELF_CORRECTION_COMPLETED:
OPEN_RISKS:
NOT_VERIFIED:
N_A_REASON:

LOCAL_FEATURE_SMOKE:
PRODUCTION_BASELINE_SMOKE:

UI_UX_QUALITY_GATE: PASS | HOLD | FAIL

OWNER_PREVIEW_APPROVAL:
CI_EXACT_HEAD:
CROSS_PR_SEMANTIC_GATE:
FINAL_MERGE_REVIEW:
OWNER_MERGE_APPROVAL:
OWNER_RELEASE_APPROVAL:
PRODUCTION_UI_PARITY:

NEXT_ACTION:

Skor target 9/10 hanya aspirasi;
jangan menghitung skor tanpa bukti
atau mengganti gate dengan skor.
