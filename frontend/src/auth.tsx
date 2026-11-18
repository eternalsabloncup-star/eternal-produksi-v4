import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  type MeResponse,
  type UserPublic,
} from "./api";
import { isSuperAdmin } from "./roles";

type AuthState = {
  user: UserPublic | null;
  csrfToken: string | null;
  loading: boolean;
  /** True when role is super_admin — full module access, no role-based menu hiding. */
  isSuperAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const applyMe = useCallback((me: MeResponse | null) => {
    if (!me) {
      setUser(null);
      setCsrfToken(null);
      return;
    }
    setUser(me.user);
    setCsrfToken(me.csrf_token);
  }, []);

  const refresh = useCallback(async () => {
    const me = await fetchMe();
    applyMe(me);
  }, [applyMe]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (!cancelled) {
          applyMe(me);
        }
      } catch {
        if (!cancelled) {
          applyMe(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const me = await apiLogin(email, password);
      applyMe(me);
    },
    [applyMe],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    applyMe(null);
  }, [applyMe]);

  const value = useMemo(
    () => ({
      user,
      csrfToken,
      loading,
      isSuperAdmin: isSuperAdmin(user?.role),
      login,
      logout,
      refresh,
    }),
    [user, csrfToken, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth harus dipakai di dalam AuthProvider");
  }
  return ctx;
}
