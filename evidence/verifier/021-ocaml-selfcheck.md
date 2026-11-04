Commit: 88ab957  Base: 7dba86e  Branch: ocaml-port
Item: the finding in inbox/017-ocaml-notes.report.md. Short review.
Claim: the promote case now uses the pe_fifos join bug; after promotion it fails with the dune rule and passes with the rule removed, and self_check.sh requires both. Delete the rule yourself and confirm self_check.sh reports the promote case missed.
