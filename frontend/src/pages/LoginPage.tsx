/**
 * pages/LoginPage.tsx — login, of (bij een verse installatie zonder account)
 * het first-time account-aanmaken-scherm: de gebruiker kiest zelf een
 * gebruikersnaam + wachtwoord i.p.v. een gegenereerd wachtwoord uit een
 * bestand te moeten vissen. GET /auth/status beslist welke variant toont.
 */
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button, Card, Input, Label } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

export function LoginPage() {
  const { login, setup } = useAuth();
  const navigate = useNavigate();
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ needs_setup: boolean }>("/auth/status")
      .then((s) => setNeedsSetup(s.needs_setup))
      .catch(() => setNeedsSetup(false));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (needsSetup) {
        if (password !== confirm) {
          setError("Wachtwoorden komen niet overeen");
          return;
        }
        await setup(username, password);
      } else {
        await login(username, password);
      }
      navigate("/labs");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : needsSetup ? "Account aanmaken mislukt" : "Inloggen mislukt");
    } finally {
      setBusy(false);
    }
  }

  if (needsSetup === null) {
    return <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">Laden…</div>;
  }

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-background">
      <Card className="w-full max-w-sm p-6">
        <h1 className="mb-1 text-xl font-bold">LabX</h1>
        {needsSetup ? (
          <p className="mb-5 text-sm text-muted-foreground">
            Welkom! Maak het beheerdersaccount aan voor deze installatie — hiermee log je voortaan in.
          </p>
        ) : (
          <p className="mb-5 text-sm text-muted-foreground">Log in met het beheerdersaccount van deze server.</p>
        )}
        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <Label>Gebruikersnaam</Label>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
          </div>
          <div>
            <Label>Wachtwoord{needsSetup ? " (minimaal 8 tekens)" : ""}</Label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {needsSetup && (
            <div>
              <Label>Wachtwoord (nogmaals)</Label>
              <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Bezig…" : needsSetup ? "Account aanmaken" : "Inloggen"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
