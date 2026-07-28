import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";
import {
  ApiError,
  clearToken,
  errorMessage,
  exchangeWebSession,
  getSystemStatus,
  setToken,
} from "../api";
import { BrandIcon } from "../components/icons";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const code = new URLSearchParams(location.search).get("code");
    if (!code) return;
    let active = true;
    setBusy(true);
    setError(null);
    void exchangeWebSession(code)
      .then(() => {
        if (active) navigate("/", { replace: true });
      })
      .catch((err: unknown) => {
        if (active) setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [location.search, navigate]);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const value = token.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    setToken(value);
    try {
      await getSystemStatus();
      navigate("/", { replace: true });
    } catch (err) {
      clearToken();
      if (err instanceof ApiError && err.status === 401) {
        setError("invalid token");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={submit}>
        <h1>
          <span className="brand-mark">
            <BrandIcon />
          </span>
          findata
        </h1>
        <p className="muted">
          {busy && new URLSearchParams(location.search).has("code")
            ? "Signing you in…"
            : "Paste the workspace token to continue."}
        </p>
        <p className="muted">
          Run <code>findata web open</code> to sign in automatically, or find the token with{" "}
          <code>findata-server token &lt;workspace&gt;</code>.
        </p>
        {error && <div className="error-banner">{error}</div>}
        <input
          type="password"
          placeholder="workspace token"
          autoFocus
          value={token}
          onChange={(e) => setTokenInput(e.target.value)}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Log in"}
        </button>
      </form>
    </div>
  );
}
