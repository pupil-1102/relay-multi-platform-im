import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError } from "./api";
import type { AuthMe } from "./types";

interface AuthContextValue {
  me: AuthMe | null;
  loading: boolean;
  refresh: () => Promise<void>;
  loginDemo: () => Promise<void>;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  registerAccount: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.me();
      setMe(data);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setMe(null);
      } else {
        setMe(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (window.location.pathname === "/login") {
      setLoading(false);
      return;
    }
    void refresh();
  }, [refresh]);

  const loginDemo = useCallback(async () => {
    const data = await api.devLogin();
    setMe(data);
  }, []);

  const loginWithPassword = useCallback(async (email: string, password: string) => {
    const data = await api.loginWithPassword({ email, password });
    setMe(data);
  }, []);

  const registerAccount = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const data = await api.register({
        email,
        password,
        display_name: displayName?.trim() || undefined,
      });
      setMe(data);
    },
    [],
  );

  const logout = useCallback(async () => {
    await api.logout();
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ me, loading, refresh, loginDemo, loginWithPassword, registerAccount, logout }),
    [me, loading, refresh, loginDemo, loginWithPassword, registerAccount, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
