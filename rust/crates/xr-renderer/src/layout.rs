//! Output layout validation (`spike/include/xr/types.hpp` `OutputSpec`, `validate_layout`).
//!
//! Read-only: output IDs are supplied by their owner and never reassigned, so removing an output leaves the others' IDs
//! as they were.

use std::fmt;

/// Caller-owned stable UUID, never an array index.
pub type OutputId = String;

/// One output of the layout, as its owner describes it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct OutputSpec {
    pub id: OutputId,
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub refresh_millihz: u32,
    pub scale: f64,
    pub x: i32,
    pub y: i32,
}

/// Why a layout was rejected: a machine-readable `code` and a plain-English `detail` (the C++ `xr::Error`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LayoutError {
    pub code: &'static str,
    pub detail: &'static str,
}

impl fmt::Display for LayoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.detail)
    }
}

impl std::error::Error for LayoutError {}

/// Check every output in order and return the first problem found: an empty ID, an ID used by an earlier output, a zero
/// width, height or refresh, or a scale that is not positive and finite.
pub fn validate_layout(outputs: &[OutputSpec]) -> Result<(), LayoutError> {
    for (i, output) in outputs.iter().enumerate() {
        if output.id.is_empty() {
            return Err(LayoutError { code: "invalid_id", detail: "Output ID must be nonempty" });
        }
        if outputs[..i].iter().any(|earlier| earlier.id == output.id) {
            return Err(LayoutError { code: "duplicate_id", detail: "Output IDs must be unique" });
        }
        if output.width == 0 || output.height == 0 || output.refresh_millihz == 0 {
            return Err(LayoutError { code: "invalid_mode", detail: "Dimensions and refresh must be positive" });
        }
        if !output.scale.is_finite() || output.scale <= 0.0 {
            return Err(LayoutError { code: "invalid_scale", detail: "Scale must be positive and finite" });
        }
    }
    Ok(())
}
