`timescale 1ns / 1ps
/* Second top-level module for timing-annotated gate-level simulation
   (flow/gl_sdf_test.sh, `make GATES=yes SDF=<file>`): back-annotates the
   netlist under tb with the SDF named by +SDF_FILE=<path>. Icarus needs
   -gspecify (cell IOPATHs) and -ginterconnect (net delays); the Makefile adds
   both. */
module sdf_annotate ();
  reg [8*4096-1:0] sdf_file;
  initial begin
    if ($value$plusargs("SDF_FILE=%s", sdf_file)) begin
      $display("sdf_annotate: %0s -> tb.user_project", sdf_file);
      $sdf_annotate(sdf_file, tb.user_project);
    end else begin
      $display("sdf_annotate: ERROR no +SDF_FILE=<path>");
      $finish;
    end
  end
endmodule
