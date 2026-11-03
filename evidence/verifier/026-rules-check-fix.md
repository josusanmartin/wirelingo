Commit: f3829df  Parent: 7e19431  Branch: rules-reread
Item: fixes to docs/rules-check.md for inbox/024-rules-check.report.md (only that file changed).
Check each 024 finding is fixed:
1. Actions disabled: GDS row and ranked fix 7 say public is not enough, Actions must be enabled; zero runs says nothing about passing; runner 4 CPU / 16 GB public vs 2 / 8 private.
2. Quotes: full "Submit your design by January 18th, 2027."; FAQ required fields marked as paraphrase; SKY130 labels on "At least 50MHz" and 66 MHz; TT clock row "met against SKY130 figures only". The reviewer also changed quotes of isa.md:635-636, board-transport.md:75, explain-wirelingo, and the template's last commit (231f26a5): check those are now verbatim/right.
3. RULES.md: no first/only/nobody/superlative left anywhere in the file (the reviewer says it also removed "nobody has silicon", "the only fix", "larger in count"). Grep the whole file.
4. Notes 4-6 and the ranked-fix judgements applied: MarcosAsh Pico host; decoded DSLogic records; new claim 17 (info.md:46-48); the post's "favorite designs" line and TT SRAM pointer (1024x8, not our 256x32); fix 2 note on model/RTL-only after fault-fixes; quad_spi.py in fix 3; 8x4 row now cites the SRAM example's info.yaml.
5. Nothing new is wrong.
Kria off limits; no SWD.
