//! C `printf` number formats, so the Rust dumps print exactly what the C++ ones do.
//!
//! `%.Nf` is Rust's `{:.N}` (both round the exact binary value, ties to even, and keep the sign of -0).
//! `%g` has no Rust equivalent; [`g`] is it.

/// C `%.{prec}g` of `x` (a `float` passed to printf is promoted to `double` first; pass `f64::from(v)`).
pub fn g(x: f64, prec: usize) -> String {
    if x.is_nan() {
        return if x.is_sign_negative() { "-nan".into() } else { "nan".into() };
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf".into() } else { "inf".into() };
    }
    let p = prec.max(1);
    // The exponent %e would print at this precision (after its rounding).
    let e = format!("{:.*e}", p - 1, x);
    let (mantissa, exp) = e.split_once('e').expect("{:e} has an exponent");
    let exp: i32 = exp.parse().expect("{:e} exponent is a number");
    let p = p as i32;
    if exp < p && exp >= -4 {
        let digits = (p - 1 - exp) as usize;
        strip(format!("{x:.digits$}"))
    } else {
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{}e{sign}{:02}", strip(mantissa.to_string()), exp.abs())
    }
}

/// Trailing zeros after a decimal point, then the point itself.
fn strip(s: String) -> String {
    if !s.contains('.') {
        return s;
    }
    s.trim_end_matches('0').trim_end_matches('.').to_string()
}

#[cfg(test)]
mod tests {
    use super::g;

    #[test]
    fn matches_printf_g() {
        // Expected strings from glibc printf("%.9g") / ("%g").
        assert_eq!(g(f64::from(0.414213568f32), 9), "0.414213568");
        assert_eq!(g(1920.0, 9), "1920");
        assert_eq!(g(f64::from(326.306885f32), 9), "326.306885");
        assert_eq!(g(-0.0, 9), "-0");
        assert_eq!(g(0.0, 9), "0");
        assert_eq!(g(1e-5, 9), "1.00000001e-05".replace("1.00000001", "1"));
        assert_eq!(g(1.5e-5, 6), "1.5e-05");
        assert_eq!(g(123456789012.0, 9), "1.23456789e+11");
        assert_eq!(g(0.0001, 6), "0.0001");
        assert_eq!(g(999999.5, 6), "1e+06");
        assert_eq!(g(100000.0, 6), "100000");
        assert_eq!(g(f64::INFINITY, 6), "inf");
    }
}
