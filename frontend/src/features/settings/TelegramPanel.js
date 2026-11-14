import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { Segmented } from '@/design/Segmented';
import { cn } from '@/design/cn';
import { useProfile } from '@/api/hooks';
import { displayName } from '@/features/profile/profile';
import { AccountAvatar, ProfileEditor } from '@/features/profile/ProfileEditor';
import { CountryPhone } from './CountryPhone';
import { DEFAULT_COUNTRY, NEED_PHONE, composePhone, splitSaved } from './countries';
import { useAuthStatus, useTelegramSettings } from './queries';
import { PORT_HINT, PROXY_TYPES, accountLine, apiStatus, codeSent, credentialsReady, proxyFields, proxyForm, proxyPayload, proxyPlaceholders, proxyStatus, qrView, verifyOutcome } from './telegram';
const BOX = 'rounded-[18px] border border-line bg-surface p-5';
const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60 disabled:opacity-50';
const BTN = 'h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60';
const err = (e, fallback) => (e instanceof ApiError ? e.detail || fallback : fallback);
const post = (path, body = {}) => api(path, { method: 'POST', json: body });
function Card({ title, pill, children, label }) {
    return (_jsxs("section", { "aria-label": label ?? title, className: BOX, children: [_jsxs("div", { className: "mb-3 flex items-center justify-between gap-3", children: [_jsx("h3", { className: "text-xl font-extrabold", children: title }), pill] }), children] }));
}
const Pill = ({ children, tone = 'muted' }) => _jsx("span", { className: cn('shrink-0 rounded-full px-3 py-1 text-xs font-bold', tone === 'ok' ? 'bg-good/15 text-good' : tone === 'warn' ? 'bg-warn/15 text-warn' : 'bg-surface-3 text-muted'), children: children });
const Label = ({ children }) => _jsx("span", { className: "text-[11px] font-bold uppercase tracking-wide text-muted", children: children });
/** Telegram: account, API credentials, proxy and sign-in (QR or phone), as in the classic Settings. */
export function TelegramPanel() {
    const settings = useTelegramSettings();
    const profile = useProfile();
    const [editProfile, setEditProfile] = useState(false);
    const auth = useAuthStatus();
    const qc = useQueryClient();
    const s = (settings.data ?? {});
    const connected = Boolean(auth.data?.connected ?? s.connected);
    const refresh = () => { void qc.invalidateQueries({ queryKey: ['settings'] }); void qc.invalidateQueries({ queryKey: ['status'] }); void qc.invalidateQueries({ queryKey: ['channels'] }); };
    // ---- API credentials
    const [apiId, setApiId] = useState('');
    const [hash, setHash] = useState('');
    const [credMsg, setCredMsg] = useState('');
    const [testMsg, setTestMsg] = useState(null);
    const [busy, setBusy] = useState('');
    const loaded = useRef(false);
    useEffect(() => { if (settings.data && !loaded.current) {
        loaded.current = true;
        setApiId(String(s.api_id ?? ''));
    } }, [settings.data, s.api_id]);
    const maskedSaved = Boolean(s.api_hash_masked);
    const saveCreds = async () => {
        setBusy('save');
        try {
            const r = await post('/api/settings/telegram', { api_id: apiId.trim(), api_hash: hash.trim() });
            setCredMsg(r.ok ? 'Saved.' : `Error: ${String(r.detail ?? 'Could not save Telegram settings.')}`);
            if (r.ok)
                setHash('');
            refresh();
            setTimeout(() => setCredMsg(''), 2500);
        }
        catch (e) {
            setCredMsg(`Error: ${err(e, 'Could not save Telegram settings.')}`);
        }
        finally {
            setBusy('');
        }
    };
    const testApi = async () => {
        setBusy('test');
        setTestMsg({ text: 'Testing Telegram API…', ok: true });
        try {
            const r = await post('/api/telegram/self-test');
            setTestMsg(r.ok ? { text: r.authorized ? 'API reachable; account already authorized.' : 'API reachable; login is not required for this test.', ok: true } : { text: `Test failed: ${String(r.detail ?? 'Telegram API connection failed.')}`, ok: false });
        }
        catch (e) {
            setTestMsg({ text: `Test failed: ${err(e, 'Telegram API connection failed.')}`, ok: false });
        }
        finally {
            setBusy('');
        }
    };
    const [confirm, setConfirm] = useState(null);
    const [recoveryMsg, setRecoveryMsg] = useState('');
    const doConfirmed = async () => {
        const kind = confirm;
        setConfirm(null);
        if (kind === 'logout') {
            setBusy('logout');
            setCredMsg('Logging out of Telegram…');
            try {
                const r = await post('/api/telegram/logout-account');
                setCredMsg(r.ok ? 'Logged out of Telegram. API credentials remain saved.' : `Error: ${String(r.detail ?? 'Could not log out of Telegram.')}`);
                refresh();
            }
            catch (e) {
                setCredMsg(`Error: ${err(e, 'Could not log out of Telegram.')}`);
            }
            finally {
                setBusy('');
            }
        }
        else if (kind === 'reset') {
            setBusy('reset');
            setRecoveryMsg('Archiving the saved session…');
            try {
                const r = await post('/api/telegram/reset-invalid-session');
                setRecoveryMsg(r.ok === false ? String(r.detail ?? 'Could not archive the session.') : 'Saved session archived. Sign in again below.');
                refresh();
            }
            catch (e) {
                setRecoveryMsg(err(e, 'Could not archive the saved session.'));
            }
            finally {
                setBusy('');
            }
        }
    };
    // ---- proxy
    const proxy = (s.proxy_settings ?? {});
    const [pf, setPf] = useState(proxyForm(undefined));
    const [proxyMsg, setProxyMsg] = useState('');
    const proxyLoaded = useRef(false);
    useEffect(() => { if (settings.data && !proxyLoaded.current) {
        proxyLoaded.current = true;
        setPf(proxyForm(proxy));
    } }, [settings.data]); // eslint-disable-line react-hooks/exhaustive-deps
    const ps = proxyStatus(proxy);
    const fields = proxyFields(pf.type);
    const ph = proxyPlaceholders(proxy);
    const setP = (k, v) => setPf((f) => ({ ...f, [k]: v }));
    const saveProxy = async () => {
        setBusy('proxy');
        setProxyMsg('Saving protected settings…');
        try {
            const r = await post('/api/settings/telegram/proxy', proxyPayload(pf));
            if (r.ok === false)
                return void setProxyMsg(`Error: ${String(r.detail ?? 'Could not save proxy settings.')}`);
            setProxyMsg(r.proxy_settings?.restart_required ? 'Saved. Restart Archive X to apply.' : 'Saved.');
            setPf((f) => ({ ...f, password: '', secret: '', username: '', clearAuth: false, clearSecret: false }));
            refresh();
        }
        catch (e) {
            setProxyMsg(`Error: ${err(e, 'Could not save proxy settings.')}`);
        }
        finally {
            setBusy('');
        }
    };
    // ---- sign in
    const [method, setMethod] = useState('qr');
    const [needPassword, setNeedPassword] = useState(null);
    const [qr, setQr] = useState(null);
    const [qrActive, setQrActive] = useState(false);
    const [loginMsg, setLoginMsg] = useState('');
    const [country, setCountry] = useState(DEFAULT_COUNTRY);
    const [local, setLocal] = useState('');
    const phoneLoaded = useRef(false);
    useEffect(() => { if (settings.data && !phoneLoaded.current) {
        phoneLoaded.current = true;
        const sp = splitSaved(String(s.phone ?? ''));
        setCountry(sp.country);
        setLocal(sp.local);
    } }, [settings.data, s.phone]);
    const fullPhone = () => composePhone(local, country).full;
    const [code, setCode] = useState('');
    const [codeAsked, setCodeAsked] = useState(false);
    const [pw, setPw] = useState('');
    const [passkey, setPasskey] = useState(false);
    const done = () => { setQrActive(false); setNeedPassword(null); setLoginMsg('Telegram login successful. Session saved locally.'); refresh(); void api('/api/telegram/refresh', { method: 'POST' }).catch(() => undefined); };
    const apply = (state) => {
        const v = qrView(state);
        setQr(v);
        if (v.kind === 'connected')
            return done();
        setQrActive(v.kind === 'waiting');
        if (v.kind === 'password') {
            setNeedPassword('qr');
            setLoginMsg(v.message);
        }
        else
            setLoginMsg(v.message);
    };
    useEffect(() => {
        if (!qrActive)
            return;
        let busyPoll = false;
        const t = setInterval(async () => {
            if (busyPoll)
                return;
            busyPoll = true;
            try {
                apply(await api('/api/telegram/qr-login/status'));
            }
            catch {
                setLoginMsg('Connection issue while checking QR login. Retrying…');
            }
            finally {
                busyPoll = false;
            }
        }, 1400);
        return () => clearInterval(t);
    }, [qrActive]); // eslint-disable-line react-hooks/exhaustive-deps
    const qrStart = async (again) => {
        if (!credentialsReady(apiId, hash, maskedSaved))
            return void setLoginMsg('Enter and save your Telegram API ID and API Hash first.');
        setBusy('qr');
        setLoginMsg(again ? 'Refreshing QR code…' : 'Generating a secure QR code…');
        try {
            apply(await post(`/api/telegram/qr-login/${again ? 'refresh' : 'start'}`));
        }
        catch (e) {
            setLoginMsg(err(e, 'Could not start QR login. Save your API credentials and retry.'));
        }
        finally {
            setBusy('');
        }
    };
    const qrCancel = async () => { setQrActive(false); try {
        await post('/api/telegram/qr-login/cancel');
    }
    catch { /* ignore */ } setQr({ kind: 'idle', message: 'Generate a short-lived code using the button below.', show: false, tone: 'info' }); setLoginMsg('QR login cancelled.'); };
    const sendCode = async () => {
        const full = fullPhone();
        if (!full)
            return void setLoginMsg(NEED_PHONE);
        setBusy('code');
        setLoginMsg('Sending code…');
        try {
            const r = await post('/api/telegram/send_code', { phone: full });
            if (codeSent(r)) {
                setCodeAsked(true);
                setLoginMsg('Code sent — check Telegram.');
            }
            else
                setLoginMsg(`Error: ${String(r.detail ?? r.error ?? 'Could not send code')}`);
        }
        catch (e) {
            setLoginMsg(`Error: ${err(e, 'Could not send code')}`);
        }
        finally {
            setBusy('');
        }
    };
    const verify = async () => {
        setBusy('verify');
        setLoginMsg('Verifying…');
        try {
            const o = verifyOutcome(await post('/api/telegram/verify_code', { phone: fullPhone(), code: code.trim() }));
            if (o.kind === 'done')
                done();
            else if (o.kind === 'password') {
                setNeedPassword('phone');
                setLoginMsg('Two-factor password required.');
            }
            else
                setLoginMsg(`Error: ${o.message}`);
        }
        catch (e) {
            setLoginMsg(`Error: ${err(e, 'Verification failed')}`);
        }
        finally {
            setBusy('');
        }
    };
    const verifyPw = async () => {
        setBusy('pw');
        setLoginMsg('Verifying 2FA password…');
        try {
            const r = await post('/api/telegram/verify_2fa', { password: pw });
            if (r.ok && !r.two_factor_required) {
                setPw('');
                done();
            }
            else
                setLoginMsg(`Error: ${String(r.detail ?? r.error ?? 'Invalid 2FA password')}`);
        }
        catch (e) {
            setLoginMsg(`Error: ${err(e, 'Invalid 2FA password')}`);
        }
        finally {
            setBusy('');
        }
    };
    const st = apiStatus(s);
    const showLogin = !connected;
    return (_jsxs("div", { className: "grid gap-4", children: [_jsxs(Card, { title: "Account", pill: _jsx(Pill, { tone: connected ? 'ok' : 'warn', children: connected ? 'Connected' : 'Not connected' }), children: [_jsxs("div", { className: "flex flex-wrap items-center gap-4", children: [_jsx(AccountAvatar, { className: "size-12 text-lg" }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("b", { className: "block text-lg", children: displayName(profile.data) || 'Telegram account' }), _jsx("small", { className: "text-sm text-muted", children: accountLine({ connected, configured: s.configured }) })] }), _jsxs("button", { type: "button", onClick: () => setEditProfile(true), className: cn(BTN, 'inline-flex items-center gap-2'), children: [_jsx(Icon, { name: "edit", className: "size-4" }), "Edit"] })] }), editProfile ? _jsx(ProfileEditor, { onClose: () => setEditProfile(false) }) : null] }), _jsxs(Card, { title: "API credentials", pill: _jsx(Pill, { tone: st.tone === 'ok' ? 'ok' : 'muted', children: s.api_verified ? 'Verified' : s.configured ? 'Saved' : 'Not verified' }), children: [_jsxs("a", { href: "https://my.telegram.org/apps", target: "_blank", rel: "noreferrer", className: "flex h-12 items-center justify-center gap-2 rounded-xl bg-accent text-[15px] font-bold text-accent-fg hover:brightness-105", children: [_jsx(Icon, { name: "send" }), "Login with Telegram", _jsx(Icon, { name: "external", className: "size-4" })] }), _jsx("p", { className: "mb-4 mt-2 text-xs text-muted", children: "Opens Telegram\u2019s official API page. Copy your API ID and API Hash below; Archive X cannot read credentials from another website automatically." }), _jsx("p", { className: "mb-3 text-center text-xs text-faint", children: "or enter manually" }), _jsxs("div", { className: "grid gap-3", children: [_jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "API ID" }), _jsx("input", { value: apiId, onChange: (e) => setApiId(e.target.value), inputMode: "numeric", placeholder: "12345678", "aria-label": "API ID", autoComplete: "off", className: INPUT })] }), _jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "API Hash" }), _jsx("input", { type: "password", value: hash, onChange: (e) => setHash(e.target.value), placeholder: maskedSaved ? String(s.api_hash_masked) : 'Paste API Hash', "aria-label": "API Hash", autoComplete: "off", className: INPUT })] })] }), _jsxs("div", { className: "mt-4 flex flex-wrap items-center gap-2.5", children: [_jsx("button", { type: "button", disabled: !!busy || !apiId.trim(), onClick: () => void saveCreds(), className: PRIMARY, children: busy === 'save' ? 'Saving…' : 'Save credentials' }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => void testApi(), className: BTN, children: busy === 'test' ? 'Testing…' : 'Test API' }), connected ? _jsx("button", { type: "button", disabled: !!busy, onClick: () => setConfirm('logout'), className: cn(BTN, 'text-bad'), children: "Log out of Telegram" }) : null, _jsxs("span", { className: cn('flex items-center gap-1.5 text-xs', st.tone === 'ok' ? 'text-good' : 'text-warn'), children: [_jsx(Icon, { name: st.tone === 'ok' ? 'check' : 'close', className: "size-3.5" }), st.text] })] }), _jsx("p", { role: "status", className: "mt-2 min-h-4 text-xs text-muted", children: credMsg }), testMsg ? _jsx("p", { role: "status", className: cn('text-xs', testMsg.ok ? 'text-good' : 'text-bad'), children: testMsg.text }) : null, _jsxs("details", { className: "mt-3 rounded-xl border border-line px-3 py-2", children: [_jsx("summary", { className: "cursor-pointer text-sm font-bold", children: "Telegram session recovery" }), _jsx("p", { className: "mt-2 text-xs text-muted", children: "Use this after Telegram reports AuthKeyDuplicatedError or says the session was used from two IP addresses. A network timeout alone does not mean the session is invalid. First stop other Archive X instances and configure a working fixed-egress proxy if needed." }), _jsx("button", { type: "button", disabled: !!busy, onClick: () => setConfirm('reset'), className: cn(BTN, 'mt-3 h-10 text-bad'), children: "Archive saved session and sign in again" }), _jsx("span", { role: "status", className: "ml-3 text-xs text-muted", children: recoveryMsg })] })] }), _jsxs(Card, { title: "Telegram proxy", pill: _jsx(Pill, { children: ps.label }), children: [_jsx("p", { className: "mb-1 text-sm text-muted", children: "Route Archive X\u2019s Telegram connection through a proxy." }), _jsx("p", { className: "mb-4 text-xs text-muted", children: ps.help }), _jsxs("div", { className: "grid gap-3 sm:grid-cols-2", children: [_jsxs("label", { className: "grid gap-1.5 sm:col-span-2", children: [_jsx(Label, { children: "Proxy type" }), _jsx("select", { value: pf.type, disabled: ps.locked, onChange: (e) => setP('type', e.target.value), "aria-label": "Proxy type", className: cn(INPUT, 'sm:max-w-xs'), children: PROXY_TYPES.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) })] }), fields.host ? _jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Server" }), _jsx("input", { value: pf.host, disabled: ps.locked, onChange: (e) => setP('host', e.target.value), placeholder: "proxy.example.com", "aria-label": "Proxy server", className: INPUT })] }) : null, fields.port ? _jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Port" }), _jsx("input", { value: pf.port, disabled: ps.locked, inputMode: "numeric", onChange: (e) => setP('port', e.target.value), placeholder: PORT_HINT[pf.type], "aria-label": "Proxy port", className: INPUT })] }) : null, fields.auth ? (_jsxs(_Fragment, { children: [_jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Username" }), _jsx("input", { value: pf.username, disabled: ps.locked, onChange: (e) => setP('username', e.target.value), placeholder: ph.username, "aria-label": "Proxy username", autoComplete: "off", className: INPUT })] }), _jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Password" }), _jsx("input", { type: "password", value: pf.password, disabled: ps.locked, onChange: (e) => setP('password', e.target.value), placeholder: ph.password, "aria-label": "Proxy password", autoComplete: "off", className: INPUT })] }), _jsxs("label", { className: "flex items-center gap-2 text-xs text-muted sm:col-span-2", children: [_jsx("input", { type: "checkbox", checked: pf.clearAuth, disabled: ps.locked, onChange: (e) => setP('clearAuth', e.target.checked), className: "accent-[var(--color-accent)]" }), "Remove the saved username and password"] })] })) : null, fields.secret ? (_jsxs(_Fragment, { children: [_jsxs("label", { className: "grid gap-1.5 sm:col-span-2", children: [_jsx(Label, { children: "MTProto secret" }), _jsx("input", { type: "password", value: pf.secret, disabled: ps.locked, onChange: (e) => setP('secret', e.target.value), placeholder: ph.secret, "aria-label": "MTProto secret", autoComplete: "off", className: INPUT })] }), _jsxs("label", { className: "flex items-center gap-2 text-xs text-muted sm:col-span-2", children: [_jsx("input", { type: "checkbox", checked: pf.clearSecret, disabled: ps.locked, onChange: (e) => setP('clearSecret', e.target.checked), className: "accent-[var(--color-accent)]" }), "Remove the saved secret"] })] })) : null] }), _jsxs("div", { className: "mt-4 flex flex-wrap items-center gap-3", children: [_jsx("button", { type: "button", disabled: ps.locked || !!busy, onClick: () => void saveProxy(), className: PRIMARY, children: busy === 'proxy' ? 'Saving…' : 'Save proxy settings' }), _jsx("span", { role: "status", className: "text-xs text-muted", children: proxyMsg })] }), _jsx("p", { className: "mt-3 text-xs text-faint", children: "Proxy credentials are encrypted in Archive X\u2019s protected settings and never returned to the browser. Restart Archive X to apply changes. For a stable source IP, use a provider with fixed egress; SOCKS5 or HTTP is recommended. MTProto uses Telethon\u2019s experimental transport." })] }), showLogin ? (_jsxs(Card, { title: "Sign in to Telegram", pill: _jsxs(Pill, { children: [_jsx(Icon, { name: "lock", className: "mr-1 inline size-3" }), "Local session"] }), children: [_jsx("p", { className: "mb-3 text-sm text-muted", children: "Not logged in: choose a method. You may be asked for the code Telegram sends, then your cloud password if two-step verification is on." }), needPassword ? (_jsxs("div", { className: "grid max-w-md gap-3", children: [_jsx("h4", { className: "text-lg font-extrabold", children: "Enter Cloud Password" }), _jsx("p", { className: "text-sm text-muted", children: "Two-Step Verification is enabled on your Telegram account. Enter your Telegram cloud password to finish signing in." }), _jsx("input", { type: "password", value: pw, onChange: (e) => setPw(e.target.value), onKeyDown: (e) => e.key === 'Enter' && void verifyPw(), placeholder: "Telegram cloud password", "aria-label": "Telegram cloud password", autoComplete: "current-password", autoFocus: true, className: INPUT }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { type: "button", disabled: !!busy || !pw, onClick: () => void verifyPw(), className: PRIMARY, children: "Verify password" }), needPassword === 'phone' ? _jsx("button", { type: "button", onClick: () => { setNeedPassword(null); setLoginMsg(''); }, className: BTN, children: "Back" }) : null] })] })) : (_jsxs(_Fragment, { children: [_jsx(Segmented, { label: "Sign-in method", value: method, onChange: (m) => { setMethod(m); setLoginMsg(''); setPasskey(false); }, options: [{ value: 'qr', label: 'QR Code' }, { value: 'phone', label: 'Phone Number' }] }), method === 'qr' ? (_jsxs("div", { className: "mt-4 grid gap-5 md:grid-cols-[minmax(0,1fr)_260px]", children: [_jsxs("div", { className: "grid content-start gap-3", children: [_jsx("h4", { className: "text-lg font-extrabold", children: "Log in to Telegram by QR Code" }), _jsx("ol", { className: "grid gap-2.5 text-sm", children: ['Open Telegram on your phone', _jsxs(_Fragment, { children: ["Go to ", _jsx("b", { children: "Settings \u2192 Devices \u2192 Link Desktop Device" })] }), 'Point your phone at this screen and confirm the login'].map((t, i) => _jsxs("li", { className: "flex items-center gap-3", children: [_jsx("span", { className: "grid size-7 place-items-center rounded-full bg-accent/15 text-xs font-bold text-accent", children: i + 1 }), t] }, i)) }), !credentialsReady(apiId, hash, maskedSaved) ? _jsx("p", { className: "text-xs text-muted", children: "Save API credentials first, then generate a QR code." }) : null, _jsx("div", { className: "flex flex-wrap gap-2", children: !qrActive ? _jsx("button", { type: "button", disabled: !!busy, onClick: () => void qrStart(false), className: PRIMARY, children: "Generate QR Code" }) : _jsxs(_Fragment, { children: [_jsx("button", { type: "button", disabled: !!busy, onClick: () => void qrStart(true), className: BTN, children: "Refresh QR Code" }), _jsx("button", { type: "button", onClick: () => void qrCancel(), className: BTN, children: "Cancel" })] }) }), _jsx("button", { type: "button", onClick: () => setPasskey(!passkey), className: "justify-self-start text-sm font-bold text-accent", children: "Log in with Passkey" }), passkey ? _jsx("p", { className: "rounded-xl bg-bg p-3 text-xs text-muted", children: "Telegram currently restricts native passkey login to supported first-party clients. Archive X cannot complete Telegram passkey authentication directly yet. Use QR Code or Phone Number instead." }) : null] }), _jsx("div", { className: "grid aspect-square w-full max-w-[260px] place-items-center justify-self-center rounded-2xl bg-white p-3", children: qr?.kind === 'waiting' && qr.image ? _jsx("img", { src: qr.image, alt: "Telegram login QR code", className: "size-full object-contain" }) : _jsxs("div", { className: "grid place-items-center gap-2 rounded-xl bg-slate-900 p-4 text-center text-xs text-slate-300", children: [_jsx(Icon, { name: "grid", className: "size-8" }), _jsx("b", { children: "QR code appears here" }), _jsx("span", { children: "Generate a short-lived code using the button." })] }) })] })) : (_jsxs("div", { className: "mt-4 grid max-w-md gap-3", children: [_jsx("h4", { className: "text-lg font-extrabold", children: "Log in with phone number" }), _jsxs("div", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Phone number" }), _jsx(CountryPhone, { country: country, local: local, onChange: (c, l) => { setCountry(c); setLocal(l); }, inputClass: INPUT })] }), _jsx("div", { children: _jsx("button", { type: "button", disabled: !!busy, onClick: () => void sendCode(), className: PRIMARY, children: busy === 'code' ? 'Sending…' : 'Continue' }) }), codeAsked ? (_jsxs("label", { className: "grid gap-1.5", children: [_jsx(Label, { children: "Telegram login code" }), _jsxs("span", { className: "flex gap-2", children: [_jsx("input", { value: code, onChange: (e) => setCode(e.target.value), onKeyDown: (e) => e.key === 'Enter' && void verify(), inputMode: "numeric", autoComplete: "one-time-code", placeholder: "Enter the code", "aria-label": "Telegram login code", className: INPUT }), _jsx("button", { type: "button", disabled: !!busy || !code.trim(), onClick: () => void verify(), className: PRIMARY, children: "Verify" })] })] })) : null] })), _jsx("p", { role: "status", "aria-live": "polite", className: "mt-3 min-h-5 text-sm text-muted", children: loginMsg })] }))] })) : (_jsx(Card, { title: "Signed in", pill: _jsx(Pill, { tone: "ok", children: "Connected" }), children: _jsx("p", { className: "text-sm text-muted", children: "Session saved and connected. Use \u201CLog out of Telegram\u201D above only to sign in again or switch account." }) })), confirm ? (_jsx(Dialog, { open: true, onClose: () => setConfirm(null), title: confirm === 'logout' ? 'Log out of Telegram?' : 'Archive the saved session?', children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: confirm === 'logout' ? 'Log out of the Telegram account on this Archive X installation? This revokes the current Telegram session. Your API ID and API Hash will remain saved. Telegram-backed operations may need you to sign in again.' : 'The saved Telegram session will be archived and a fresh authorization will be needed. Your API ID and API Hash stay saved.' }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setConfirm(null), className: BTN, children: "Keep session" }), _jsx("button", { type: "button", onClick: () => void doConfirmed(), className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: confirm === 'logout' ? 'Log out of Telegram' : 'Archive session' })] })] }) })) : null] }));
}
