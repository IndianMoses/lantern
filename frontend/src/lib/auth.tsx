import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setCsrf } from "./api";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "customer" | "insurer_handler" | "insurer_admin" | "platform_admin";
  locale: "en" | "zh";
  org: { id: string; code: string; name: string; name_zh?: string } | null;
  is_demo: boolean;
}

interface AuthCtx {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (data: { email: string; password: string; full_name: string; locale: string; country: string }) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  setLocale: (locale: "en" | "zh") => Promise<void>;
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

type MeResponse = { user: User; csrf_token: string };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const me = await api.get<MeResponse>("/api/auth/me");
      setCsrf(me.csrf_token);
      setUser(me.user);
    } catch {
      setCsrf(null);
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const me = await api.post<MeResponse>("/api/auth/login", { email, password });
    setCsrf(me.csrf_token);
    setUser(me.user);
    return me.user;
  }, []);

  const register = useCallback(async (data: { email: string; password: string; full_name: string; locale: string; country: string }) => {
    const me = await api.post<MeResponse>("/api/auth/register", data);
    setCsrf(me.csrf_token);
    setUser(me.user);
    return me.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } finally {
      setCsrf(null);
      setUser(null);
    }
  }, []);

  const setLocale = useCallback(async (locale: "en" | "zh") => {
    const me = await api.patch<MeResponse>("/api/auth/me", { locale });
    setUser(me.user);
  }, []);

  return <Ctx.Provider value={{ user, ready, login, register, logout, refresh, setLocale }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}
