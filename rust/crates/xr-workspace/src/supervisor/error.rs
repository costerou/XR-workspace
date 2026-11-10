//! Why a video, web video or immersive request was refused, shared by those modules.

use std::fmt;

/// Why a request or file was refused.
#[derive(Debug, Clone, PartialEq)]
pub enum Error {
    /// A malformed request or payload (Python ValueError). Not meant for the user.
    Invalid(&'static str),
    /// The video cannot be shown (Python VideoRejected): the message is shown to the user.
    Rejected(String),
    /// ffprobe could not read a web link (web_video.ProbeFailed); `kind` is `invalid`, `http` or `other`.
    ProbeFailed { message: String, kind: &'static str },
    /// yt-dlp found no video on the page (web_video.PageUnsupported).
    PageUnsupported(String),
}

impl Error {
    pub fn rejected(text: impl Into<String>) -> Error {
        Error::Rejected(text.into())
    }

    /// The message (what Python's `str(exc)` gives).
    pub fn message(&self) -> &str {
        match self {
            Error::Invalid(m) => m,
            Error::Rejected(m) | Error::PageUnsupported(m) => m,
            Error::ProbeFailed { message, .. } => message,
        }
    }

    /// A user-facing refusal (VideoRejected or one of its subclasses), not a malformed request.
    pub fn is_rejection(&self) -> bool {
        !matches!(self, Error::Invalid(_))
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;
