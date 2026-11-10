//! `spike/tests/layout_test.cpp`: synthetic offline layouts.

use xr_renderer::layout::{OutputSpec, validate_layout};

fn output(id: &str, name: &str, x: i32) -> OutputSpec {
    OutputSpec {
        id: id.into(),
        name: name.into(),
        width: 1920,
        height: 1080,
        refresh_millihz: 120_000,
        scale: 1.0,
        x,
        y: 0,
    }
}

fn three() -> Vec<OutputSpec> {
    vec![
        output("71ed078f-d0aa-49a7-b8b2-000000000001", "synthetic-a", 0),
        output("71ed078f-d0aa-49a7-b8b2-000000000002", "synthetic-b", 1920),
        output("71ed078f-d0aa-49a7-b8b2-000000000003", "synthetic-c", 3840),
    ]
}

/// The two outputs left after removing the middle one.
fn valid() -> Vec<OutputSpec> {
    let mut outputs = three();
    outputs.remove(1);
    outputs
}

#[test]
fn valid_layout_accepted() {
    assert_eq!(validate_layout(&three()), Ok(()));
}

#[test]
fn removal_keeps_layout_valid_and_ids_stable() {
    let outputs = valid();
    assert_eq!(validate_layout(&outputs), Ok(()));
    assert_eq!(outputs[0].id, "71ed078f-d0aa-49a7-b8b2-000000000001");
    assert_eq!(outputs[1].id, "71ed078f-d0aa-49a7-b8b2-000000000003");
}

fn rejected_code(change: impl FnOnce(&mut Vec<OutputSpec>)) -> &'static str {
    let mut outputs = valid();
    change(&mut outputs);
    validate_layout(&outputs).expect_err("layout accepted").code
}

#[test]
fn duplicate_ids_rejected() {
    assert_eq!(rejected_code(|o| o[1].id = o[0].id.clone()), "duplicate_id");
}

#[test]
fn empty_id_rejected() {
    assert_eq!(rejected_code(|o| o[0].id.clear()), "invalid_id");
}

#[test]
fn zero_mode_rejected() {
    assert_eq!(rejected_code(|o| o[0].width = 0), "invalid_mode");
    assert_eq!(rejected_code(|o| o[0].height = 0), "invalid_mode");
    assert_eq!(rejected_code(|o| o[0].refresh_millihz = 0), "invalid_mode");
}

#[test]
fn invalid_scale_rejected() {
    for scale in [0.0, -1.0, f64::INFINITY, f64::NAN] {
        assert_eq!(rejected_code(|o| o[0].scale = scale), "invalid_scale", "scale {scale}");
    }
}

#[test]
fn fractional_scale_accepted() {
    let mut outputs = valid();
    outputs[0].scale = 1.5;
    assert_eq!(validate_layout(&outputs), Ok(()));
}

#[test]
fn first_problem_wins_and_reads_plainly() {
    // The checks run output by output, in the C++ order: an empty ID is reported before a bad mode on the same output.
    let mut outputs = valid();
    outputs[0].id.clear();
    outputs[0].width = 0;
    let error = validate_layout(&outputs).unwrap_err();
    assert_eq!(error.to_string(), "invalid_id: Output ID must be nonempty");
    assert_eq!(validate_layout(&[]), Ok(()));
}
