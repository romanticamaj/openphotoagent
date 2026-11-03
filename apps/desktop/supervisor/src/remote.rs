//! Desktop app as a client of a self-hosted control plane (v0.5; 08 §9.2, 09b MU-DEPLOY-002).
//!
//! `<data dir>/connection.json` = `{"mode": "remote", "url": "https://photos.example.lan"}` (or the env
//! `OPAGENT_REMOTE_URL`). In remote mode the shell starts **no** local database, API or worker: the window opens the
//! control plane's own web app (same origin, so its Secure session cookie and CSRF token just work) and the person
//! signs in there. Only https is accepted, except loopback http for a CP on this machine.

/// What the shell starts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    Local,
    Remote { origin: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartPlan {
    pub postgres: bool,
    pub api: bool,
    pub worker: bool,
    /// `None`: the bundled SPA talking to the local sidecar.
    pub window_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RemoteError {
    NotHttps,
    Credentials,
    NoHost,
    Malformed,
}

/// `https://host[:port]` only: no user info, path, query or fragment survive (the origin is what we trust).
pub fn parse_origin(url: &str) -> Result<String, RemoteError> {
    let url = url.trim();
    let (scheme, rest) = url.split_once("://").ok_or(RemoteError::Malformed)?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.contains('@') {
        return Err(RemoteError::Credentials);
    }
    if authority.is_empty() {
        return Err(RemoteError::NoHost);
    }
    let host = if authority.starts_with('[') {
        authority
            .split(']')
            .next()
            .unwrap_or("")
            .trim_start_matches('[')
    } else {
        authority.split(':').next().unwrap_or("")
    };
    if host.is_empty()
        || authority
            .chars()
            .any(|c| c.is_whitespace() || c.is_control())
    {
        return Err(RemoteError::Malformed);
    }
    let loopback = matches!(host, "127.0.0.1" | "localhost" | "::1");
    match scheme.to_ascii_lowercase().as_str() {
        "https" => {}
        "http" if loopback => {}
        _ => return Err(RemoteError::NotHttps),
    }
    Ok(format!(
        "{}://{}",
        scheme.to_ascii_lowercase(),
        authority.to_ascii_lowercase()
    ))
}

/// Mode from the environment override, else `connection.json`; anything unreadable means local (the safe default).
pub fn mode(
    env: &dyn Fn(&str) -> Option<String>,
    connection_json: Option<&str>,
) -> Result<Mode, RemoteError> {
    let from_file = connection_json.and_then(|t| {
        let mode = field(t, "mode")?;
        if mode == "remote" {
            field(t, "url")
        } else {
            None
        }
    });
    match env("OPAGENT_REMOTE_URL")
        .filter(|u| !u.is_empty())
        .or(from_file)
    {
        Some(u) => Ok(Mode::Remote {
            origin: parse_origin(&u)?,
        }),
        None => Ok(Mode::Local),
    }
}

pub fn plan(mode: &Mode) -> StartPlan {
    match mode {
        Mode::Local => StartPlan {
            postgres: true,
            api: true,
            worker: true,
            window_url: None,
        },
        Mode::Remote { origin } => StartPlan {
            postgres: false,
            api: false,
            worker: false,
            window_url: Some(format!("{origin}/")),
        },
    }
}

/// Navigation allowed in the main window: the control plane's origin only (no drive-by to other sites).
pub fn may_navigate(mode: &Mode, url: &str) -> bool {
    match mode {
        Mode::Local => true,
        Mode::Remote { origin } => {
            url == origin
                || url
                    .strip_prefix(origin.as_str())
                    .is_some_and(|r| r.starts_with(['/', '?', '#']))
        }
    }
}

/// A tiny reader for the two string fields of connection.json (the crate has no JSON dependency on purpose).
fn field(text: &str, key: &str) -> Option<String> {
    let at = text.find(&format!("\"{key}\""))?;
    let rest = &text[at + key.len() + 2..];
    let rest = rest
        .trim_start()
        .strip_prefix(':')?
        .trim_start()
        .strip_prefix('"')?;
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_env(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn mu_deploy_002_remote_mode_starts_no_local_services() {
        let m = mode(
            &no_env,
            Some(r#"{"mode": "remote", "url": "https://Photos.Example.lan/some/path?x=1"}"#),
        )
        .unwrap();
        assert_eq!(
            m,
            Mode::Remote {
                origin: "https://photos.example.lan".into()
            }
        );
        let p = plan(&m);
        assert!(!p.postgres && !p.api && !p.worker);
        assert_eq!(p.window_url.as_deref(), Some("https://photos.example.lan/"));
        assert_eq!(
            plan(&Mode::Local),
            StartPlan {
                postgres: true,
                api: true,
                worker: true,
                window_url: None
            }
        );
        assert_eq!(mode(&no_env, None).unwrap(), Mode::Local);
        assert_eq!(
            mode(&no_env, Some(r#"{"mode": "local"}"#)).unwrap(),
            Mode::Local
        );
        let env =
            |k: &str| (k == "OPAGENT_REMOTE_URL").then(|| "https://cp.example:8443".to_string());
        assert_eq!(
            mode(&env, None).unwrap(),
            Mode::Remote {
                origin: "https://cp.example:8443".into()
            }
        );
    }

    #[test]
    fn mu_deploy_002_only_https_or_loopback() {
        assert_eq!(
            parse_origin("http://photos.example.lan"),
            Err(RemoteError::NotHttps)
        );
        assert_eq!(parse_origin("ftp://x"), Err(RemoteError::NotHttps));
        assert_eq!(
            parse_origin("https://user:pw@cp.example"),
            Err(RemoteError::Credentials)
        );
        assert_eq!(parse_origin("https://"), Err(RemoteError::NoHost));
        assert_eq!(parse_origin("cp.example"), Err(RemoteError::Malformed));
        assert_eq!(
            parse_origin("http://127.0.0.1:47800/").unwrap(),
            "http://127.0.0.1:47800"
        );
        assert_eq!(
            parse_origin("http://[::1]:47800").unwrap(),
            "http://[::1]:47800"
        );
    }

    #[test]
    fn mu_deploy_002_window_stays_on_the_control_plane() {
        let m = Mode::Remote {
            origin: "https://cp.example".into(),
        };
        assert!(may_navigate(&m, "https://cp.example/"));
        assert!(may_navigate(&m, "https://cp.example/login?next=%2F"));
        assert!(!may_navigate(&m, "https://cp.example.evil.com/"));
        assert!(!may_navigate(&m, "https://evil.com/?https://cp.example"));
        assert!(may_navigate(&Mode::Local, "tauri://localhost/"));
    }
}
