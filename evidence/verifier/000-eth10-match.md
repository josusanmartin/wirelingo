Commit: 61af9b2b23eb1204cb6b14f83559484eb6995bb7  Base: 5149c77
Item: firmware/eth10_match.pasm, test/model/test_eth10_match.py, docs/eth10-cut-through.md, the eth10_cut part of fpga/kv260/host/legacy_demo.py.
Claims:
1. On the cycle model the pin rises 10.0-10.9 clocks (201-218 ns at 50 MHz) after the field's last mid-bit transition.
2. A field below the threshold, or another EtherType, raises nothing; a field equal to the threshold raises it.
3. The pin rises about 38 us before a 64-byte frame ends.
4. The doc's limits are complete and nothing in it over-claims. You cannot rerun the KV260 row; check that the doc labels it properly.
