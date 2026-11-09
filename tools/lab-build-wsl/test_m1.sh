O=$(ls -d ~/bento/out/m1-* | tail -1)
B=~/bento/stock-$(sed -n 's/.*"base_label": "stock-\([0-9.]*\)".*/\1/p' "$O/manifest.json" | head -1)   # the build's base
[ -f "$B/bento1.bin" ] || B=~/bento/stock-1.6.1
# S2/S3: a build that leaves feature groups out of the image (manifest "omitted": groups not ported to its
# base yet) runs only the suites whose groups it contains. Each suite: the groups it needs in the image.
OMIT=$(python3 -c "import json,sys; print(' '.join(json.load(open(sys.argv[1])).get('omitted', [])))" "$O/manifest.json")
declare -A NEEDS=([test_diag.py]="" [test_slice.py]="" [test_polyat.py]="poly_cap" [test_conditions.py]="cond_engine"
  [test_holdstep.py]="step" [test_controls.py]="hold" [test_retrig.py]="retrig" [test_step.py]="step" [test_mod.py]="retrig"
  [test_notemod.py]="nmod retrig" [test_notemod_route.py]="nmod retrig" [test_notemod_real.py]="step nmod"
  [test_s3_m7.py]="poly_cap retrig cond_engine nmod" [test_step_xml.py]="step" [test_keys.py]=""
  [test_analog.py]="analog" [test_inst_xml.py]="analog" [test_synth_ui.py]="analog" [test_handles.py]="analog"   # S6a, BE4.22: 1.6.8 only
  [test_note0.py]="poly_cap retrig cond_engine nmod")   # BE4.22: also runs where note0 is left out (expects stock there)
# S4+S5: the mx group (Mod Matrix page, External INST rows) is left out on 1.6.8: test_step and test_mod skip their mx
# classes themselves (manifest "omitted"), so no suite needs mx as a whole.
cd /mnt/c/Users/rjrom/bento_mods/bento_enhanced/mods/m1
for t in test_diag.py test_polyat.py test_conditions.py test_holdstep.py test_controls.py test_retrig.py test_step.py test_mod.py test_notemod.py test_notemod_route.py test_notemod_real.py test_slice.py test_s3_m7.py test_step_xml.py test_keys.py test_analog.py test_inst_xml.py test_synth_ui.py test_handles.py test_note0.py; do
  echo "== $t"
  miss=$(for g in ${NEEDS[$t]}; do case " $OMIT " in *" $g "*) echo -n "$g ";; esac; done)
  if [ -n "$miss" ]; then echo "not run: this build leaves out ${miss% } (omitted: $OMIT)"; continue; fi
  ~/bento/venv/bin/python $t "$B/bento1.bin" "$O" 2>&1 | tail -15
done
