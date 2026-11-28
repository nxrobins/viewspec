use thiserror::Error;

/// Errors deliberately omit page values, URLs, JavaScript exception text and CDP payloads.
#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum Error {
    #[error("browser I/O failed")]
    Io,
    #[error("browser transport closed")]
    Closed,
    #[error("CDP command timed out: {0}")]
    Timeout(String),
    #[error("invalid or oversized CDP message")]
    InvalidMessage,
    #[error("CDP rejected {method} (code {code})")]
    Protocol { method: String, code: i64 },
    #[error("JavaScript evaluation failed")]
    JavaScript,
    #[error("invalid argument")]
    InvalidArgument,
    #[error("unsupported control or operation")]
    Unsupported,
    #[error("navigation failed: {0}")]
    Navigation(String),
    #[error("observation belongs to a different page or navigation")]
    StaleObservation,
    #[error("observed control was replaced or changed identity")]
    TargetChanged,
    #[error("control is not uniquely identified or actionable")]
    NotActionable,
    #[error("input may have been delivered; do not automatically retry")]
    ActionUncertain,
}

impl From<std::io::Error> for Error {
    fn from(_: std::io::Error) -> Self {
        Self::Io
    }
}

pub type Result<T> = std::result::Result<T, Error>;
