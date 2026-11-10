//! Holds `xr_wire::json::parse` to the verdicts in `rust/corpus/json/strict_json.json`: what Python's
//! `strict_json` and C++ `decode_control` accept and refuse, and the decided rule where they differ
//! (`tests/native_spike/strict_json_corpus.py` makes the file).

use serde_json::Value;
use xr_wire::json::{Limits, MAX_DEPTH, parse};

const LIMITS: Limits = Limits {
    max_bytes: 16384,
    max_depth: MAX_DEPTH,
};

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn rust_gives_the_corpus_verdict_for_every_case() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/json/strict_json.json"
    );
    let corpus: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(corpus["max_depth"], MAX_DEPTH);
    let cases = corpus["cases"].as_array().unwrap();
    assert!(cases.len() > 3000, "corpus too small: {}", cases.len());

    let (mut wrong, mut agreed, mut diverged) = (Vec::new(), 0, 0);
    for case in cases {
        let bytes = match (case["text"].as_str(), case["hex"].as_str()) {
            (Some(text), None) => text.as_bytes().to_vec(),
            (None, Some(h)) => hex(h),
            _ => panic!("case without text or hex: {case}"),
        };
        assert!(
            bytes.len() <= LIMITS.max_bytes,
            "case past the size limit: {case}"
        );
        let expected = case["rust"].as_str().unwrap();
        match case.get("divergence") {
            None => {
                assert_eq!(case["python"], expected);
                assert_eq!(case["cpp"], expected);
                agreed += 1;
            }
            Some(_) => diverged += 1,
        }
        let got = match parse(&bytes, LIMITS) {
            Ok(_) => "accepted",
            Err(_) => "refused",
        };
        if got != expected {
            wrong.push(format!(
                "{}: expected {expected}, got {got}",
                String::from_utf8_lossy(&bytes)
            ));
        }
    }
    println!(
        "{} cases: {agreed} where Python and C++ agree, {diverged} decided by a divergence rule",
        cases.len()
    );
    assert!(
        wrong.is_empty(),
        "{} cases differ:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}
