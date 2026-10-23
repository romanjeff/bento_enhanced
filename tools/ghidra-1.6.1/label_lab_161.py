"""Apply bento-community-lab's documented 1.6.1 addresses as Ghidra symbols.

Names are prefixed `lab_` so provenance is visible in the listing; each also
gets a plate comment naming the source. FUNCS are function entry points
(a function is created if Ghidra hasn't found one). SITES are hook sites,
tables or data inside other functions -- labelled only, never turned into
functions. Re-runnable: existing lab_ labels are replaced, nothing else touched.
Source of every entry: docs/bento-os.md in bento_enhanced.
"""
import os

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")

from ghidra.base.project import GhidraProject
from ghidra.program.flatapi import FlatProgramAPI
from ghidra.program.model.listing import CodeUnit
from ghidra.program.model.symbol import SourceType

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "bento161"

FUNCS = {
    # M4 UI / model
    0x08104C08: "ui_bump_alloc",
    0x081639A8: "param_registry_construct",
    0x0812DFE8: "seq_root_lookup",
    0x0812E070: "note_assign_id_insert",
    0x08136FB4: "notes_bulk_delete_undo",
    0x081338DC: "undo_snapshot",
    0x08136544: "notes_upsert_publish",
    0x0813325C: "mod_command_emit",
    0x081332A4: "target_propagate",
    0x081576B8: "automation_target_map",
    0x08170656: "pbar_text_override_set",
    0x08171C1C: "pbar_key_configure",
    0x08171108: "pbar_restore_labels",
    0x08171640: "pbar_restore_titles",
    0x08171E44: "pbar_encoder_turn",
    0x08177D48: "menu_construct",
    0x0817818C: "menu_apply",
    0x08177EE2: "menu_action_emit",
    0x08134800: "seq_header_generate",
    0x08180198: "header_construct",
    0x0817FAF8: "header_build_buttons",
    0x0817FE70: "header_action_emit",
    0x08183854: "draw_waveform",
    0x08168768: "draw_envelope",
    0x0817DDFC: "draw_lfo",
    0x0816A97C: "draw_button",
    0x0817E780: "draw_label",
    0x08179120: "draw_rect",
    0x081152C0: "HAL_GetTick",
    0x0815930C: "pianoroll_construct",
    0x0815A598: "pianoroll_handle_event",
    0x081599BC: "pianoroll_header_refresh",
    0x0815ABB8: "pianoroll_project_notes",
    0x0815D600: "oneshot_seq_event",
    0x0815D4B4: "oneshot_seq_event_entry",
    0x0815BC4C: "oneshot_seq_step_action",
    0x08141228: "undo_menu_construct",
    0x08114D74: "m4_mpu_setup",
    0x08152144: "sample_editor_setup",
    0x08152C50: "sample_editor_param_event",
    0x08152ACC: "sample_editor_select_pad",
    0x08152BB0: "sample_editor_open_load",
    0x0816FAEC: "seq_paint",
    # M7 audio / playback
    0x08060820: "timed_note_process",
    0x0805F8E0: "note_route",
    0x08060B64: "probability_gate",
    0x08061740: "condition_counter",
    0x0808FA90: "note_accept_0x12",
    0x08050F20: "velocity_normalize",
    0x08094760: "voice_trigger",
    0x08092F20: "voice_mod_params_copy",
    0x0809FD00: "mod_source_attach",
    0x0809FD80: "mod_param_eval",
    0x080AC66C: "sampler_root",
    0x080A21C0: "clock_convert_a",
    0x080A2200: "clock_convert_b",
    0x080A34F4: "sdiv",
    0x08095DA0: "note_cursor_copy",
    0x080A3DFC: "m7_heap_alloc",
    0x080A3BAC: "m7_heap_free_check",
    0x0804FA00: "m7_mpu_setup",
}

SITES = {
    0x0808D1FE: "pad_input_hook_site",
    0x08082221: "queue_enqueue_thumb",  # Lab lists the Thumb (odd) pointer
    0x2401BD3C: "queue_producer_global",
    0x0813F98C: "ui_outer_consumer_site",
    0x0813BC3D: "select_track_thumb",
    0x080A0510: "per_track_dsp_insert_site",
    0x0815D57E: "oneshot_seq_arrow_branch",
    0x0815C27C: "oneshot_seq_step_header",
    0x0815BDE2: "oneshot_seq_lights",
    0x08060B9A: "accepted_note_site",
    0x0812F5EA: "project_params_save_site",
    0x0812FAAC: "project_params_load_site",
    0x080BDBEC: "step_size_table",
    0x0819E428: "shared_mod_target_table",
    0x0819E7B0: "pianoroll_vtable",
    0x0819E858: "seq_paint_vtable_slot",
    0x08198DB0: "undo_menu_table_src",
    0x081B3E38: "m4_reset_data",
    0x08040588: "heap_end_literal_startup",
    0x080A3BDC: "heap_end_literal_free",
    0x080A3E94: "heap_end_literal_alloc",
    0x3000004C: "ui_bump_cursor",
    0x30000048: "ui_bump_end",
    0x3001FE2C: "hal_tick_counter",
    0x30031484: "generic_descriptors",
}

project = GhidraProject.openProject(HERE, NAME, False)
program = project.openProgram("/", NAME, False)
api = FlatProgramAPI(program)
listing = program.getListing()
st = program.getSymbolTable()
A = program.getAddressFactory().getDefaultAddressSpace().getAddress

tx = program.startTransaction("lab labels")
made = renamed = labelled = 0
missing = []
for raw, name in list(FUNCS.items()) + list(SITES.items()):
    a = A(raw & ~1)
    if not program.getMemory().contains(a):
        missing.append(hex(raw)); continue
    for s in list(st.getSymbols(a)):
        if s.getName().startswith("lab_"):
            s.delete()
    label = "lab_" + name
    if raw in FUNCS:
        f = api.getFunctionAt(a)
        if f is None:
            f = api.createFunction(a, label)
            if f is None:  # inside a function Ghidra bounded differently
                api.createLabel(a, label, True, SourceType.USER_DEFINED)
                missing.append(hex(raw) + "(no-func)")
            else:
                made += 1
        else:
            f.setName(label, SourceType.USER_DEFINED); renamed += 1
    else:
        api.createLabel(a, label, False, SourceType.USER_DEFINED); labelled += 1
    cu = listing.getCodeUnitAt(a)
    if cu is not None:
        cu.setComment(CodeUnit.PLATE_COMMENT, "bento-community-lab (1.6.1); see bento_enhanced/docs/bento-os.md")
program.endTransaction(tx, True)
project.save(program)
print(f"functions created={made} renamed={renamed} site labels={labelled} unmapped={missing}")
project.close()
