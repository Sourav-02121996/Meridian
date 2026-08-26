import { FormEvent, useState } from 'react';
import {
  ArrowRight,
  Check,
  Compass,
  FileText,
  House,
  Info,
  LayoutDashboard,
  LogOut,
  Mail,
  Menu,
  MessageSquare,
  Settings,
  ShieldCheck,
  Sparkles,
  Target,
  UserRound,
  X,
  Zap,
} from 'lucide-react';
import Dashboard from './App';

type View = 'home' | 'dashboard';

export default function Site() {
  const [email, setEmail] = useState(() => localStorage.getItem('meridian-email') ?? '');
  const [view, setView] = useState<View>('home');
  const [loginOpen, setLoginOpen] = useState(false);
  const openDashboard = () => (email ? setView('dashboard') : setLoginOpen(true));
  const goToSection = (id: string) => {
    setView('home');
    window.setTimeout(() => document.getElementById(id)?.scrollIntoView(), 0);
  };
  const login = (nextEmail: string) => {
    localStorage.setItem('meridian-email', nextEmail);
    setEmail(nextEmail);
    setLoginOpen(false);
    setView('dashboard');
  };
  const logout = () => {
    localStorage.removeItem('meridian-email');
    setEmail('');
    setView('home');
  };
  return (
    <div className="flex min-h-screen flex-col bg-paper text-ink">
      <Header
        email={email}
        onHome={() => setView('home')}
        onSection={goToSection}
        onDashboard={openDashboard}
        onLogin={() => setLoginOpen(true)}
        onLogout={logout}
      />
      {view === 'home' ? (
        <Home onStart={openDashboard} onLogin={() => setLoginOpen(true)} />
      ) : (
        <Dashboard />
      )}
      <Footer onAbout={() => goToSection('about')} />
      {loginOpen && <LoginDialog onClose={() => setLoginOpen(false)} onLogin={login} />}
    </div>
  );
}

function Brand() {
  return (
    <span className="flex items-center gap-3">
      <img
        src="/favicon-512.png"
        alt=""
        className="h-10 w-10 rounded-xl border border-black/10 object-contain"
      />
      <span className="font-display text-xl font-extrabold tracking-[-0.04em]">Meridian</span>
    </span>
  );
}

function Header({
  email,
  onHome,
  onSection,
  onDashboard,
  onLogin,
  onLogout,
}: {
  email: string;
  onHome: () => void;
  onSection: (id: string) => void;
  onDashboard: () => void;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const goToSection = (id: string) => {
    setMenuOpen(false);
    onSection(id);
  };
  const choose = (action: () => void) => {
    setMenuOpen(false);
    setAccountOpen(false);
    action();
  };
  return (
    <header className="sticky top-0 z-40 border-b border-black/10 bg-white/90 backdrop-blur-xl">
      {(menuOpen || accountOpen) && (
        <button
          className="fixed inset-0 z-[-1] cursor-default"
          aria-label="Close menu"
          onClick={() => {
            setMenuOpen(false);
            setAccountOpen(false);
          }}
        />
      )}
      <div className="relative mx-auto flex h-16 max-w-[1500px] items-center gap-3 px-5 lg:px-10">
        <div className="relative">
          <button
            className="icon-button"
            onClick={() => {
              setMenuOpen((open) => !open);
              setAccountOpen(false);
            }}
            aria-label="Open navigation menu"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={20} /> : <Menu size={21} />}
          </button>
          {menuOpen && (
            <nav className="dropdown-panel left-0" aria-label="Navigation menu">
              <button className="dropdown-item" onClick={() => choose(onHome)}>
                <House size={17} /> Home
              </button>
              <button className="dropdown-item" onClick={() => goToSection('how-it-works')}>
                <Compass size={17} /> How it works
              </button>
              <button className="dropdown-item" onClick={() => goToSection('about')}>
                <Info size={17} /> About
              </button>
              {email && (
                <button className="dropdown-item" onClick={() => choose(onDashboard)}>
                  <LayoutDashboard size={17} /> Dashboard
                </button>
              )}
            </nav>
          )}
        </div>
        <button onClick={onHome} aria-label="Meridian home">
          <Brand />
        </button>
        {email ? (
          <div className="relative ml-auto flex items-center gap-2">
            <button
              className="icon-button login-circle"
              onClick={() => {
                setAccountOpen((open) => !open);
                setMenuOpen(false);
              }}
              title="Account menu"
              aria-label="Open account menu"
              aria-expanded={accountOpen}
            >
              <UserRound size={19} />
            </button>
            {accountOpen && (
              <div className="dropdown-panel right-0 w-64">
                <div className="border-b border-black/10 px-4 py-3">
                  <p className="text-xs font-semibold uppercase tracking-wider text-black/40">
                    Signed in as
                  </p>
                  <p className="mt-1 truncate text-sm font-bold">{email}</p>
                </div>
                <div className="p-2">
                  <button className="dropdown-item" onClick={() => choose(onDashboard)}>
                    <UserRound size={17} /> Account
                  </button>
                  <button className="dropdown-item" onClick={() => choose(onDashboard)}>
                    <Settings size={17} /> Settings
                  </button>
                  <a
                    className="dropdown-item"
                    href="mailto:hello@meridian.app?subject=Meridian feedback"
                    onClick={() => setAccountOpen(false)}
                  >
                    <MessageSquare size={17} /> Feedback
                  </a>
                </div>
                <div className="border-t border-black/10 p-2">
                  <button
                    className="dropdown-item text-red-700 hover:bg-red-50"
                    onClick={() => choose(onLogout)}
                  >
                    <LogOut size={17} /> Log out
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <button
            className="icon-button login-circle ml-auto border-sage bg-sage text-white hover:bg-ink"
            onClick={onLogin}
            title="Login"
            aria-label="Login"
          >
            <UserRound size={19} />
          </button>
        )}
      </div>
    </header>
  );
}

function Home({ onStart, onLogin }: { onStart: () => void; onLogin: () => void }) {
  const matches = [
    ['94', 'Product Design Intern', 'Northstar Studio'],
    ['89', 'Junior Software Engineer', 'Common Ground'],
    ['86', 'Data Analyst — New Grad', 'Index Labs'],
  ];
  const steps = [
    [
      FileText,
      '01',
      'Bring your story',
      'Paste your résumé or upload a PDF. Your career context becomes the compass.',
    ],
    [
      Compass,
      '02',
      'Discover your direction',
      'Search roles, compare requirements, and surface opportunities aligned with your skills.',
    ],
    [
      Target,
      '03',
      'Make your move',
      'Track every application and focus your energy on the strongest matches.',
    ],
  ];
  return (
    <main className="flex-1 overflow-hidden">
      <section className="hero-grid border-b border-black/10">
        <div className="mx-auto grid min-h-[680px] max-w-[1500px] items-center gap-14 px-5 py-20 lg:grid-cols-[1.08fr_.92fr] lg:px-10">
          <div className="relative z-10">
            <div className="eyebrow mb-7 w-fit">
              <Zap size={14} fill="currentColor" /> Built for ambitious students
            </div>
            <h1 className="max-w-4xl font-display text-5xl font-extrabold leading-[.94] tracking-[-0.065em] sm:text-7xl lg:text-[92px]">
              Find work that <span className="underlined-word">fits.</span>
            </h1>
            <p className="mt-8 max-w-xl text-lg leading-8 text-black/60 sm:text-xl">
              Meridian turns a noisy job search into a focused pipeline—matching opportunities to
              your experience and showing you where to improve.
            </p>
            <div className="mt-10 flex flex-wrap gap-3">
              <button className="btn btn-dark btn-large" onClick={onStart}>
                Start your search <ArrowRight size={18} />
              </button>
              <a className="btn btn-outline btn-large" href="#how-it-works">
                See how it works
              </a>
            </div>
            <div className="mt-10 flex flex-wrap gap-x-6 gap-y-3 text-sm font-semibold text-black/55">
              {['Private by design', 'Clear match insights', 'You stay in control'].map((item) => (
                <span className="flex items-center gap-2" key={item}>
                  <Check size={16} />
                  {item}
                </span>
              ))}
            </div>
          </div>
          <div className="relative mx-auto w-full max-w-xl lg:mx-0">
            <div className="absolute -left-8 -top-8 h-28 w-28 rounded-full border border-black/20 bg-white dot-pattern" />
            <div className="relative rotate-1 border-2 border-black bg-white p-4 shadow-[14px_14px_0_#111] sm:p-6">
              <div className="flex items-center justify-between border-b border-black/10 pb-4">
                <div className="flex gap-2">
                  <i className="window-dot" />
                  <i className="window-dot" />
                  <i className="window-dot" />
                </div>
                <span className="text-xs font-bold uppercase tracking-[.2em] text-black/45">
                  Your next move
                </span>
              </div>
              <div className="space-y-4 py-5">
                {matches.map(([score, role, company]) => (
                  <div
                    className="group flex items-center gap-4 border border-black/10 p-4 transition hover:-translate-y-1 hover:border-black hover:shadow-[5px_5px_0_#111]"
                    key={role}
                  >
                    <span className="match-score grid h-12 w-12 shrink-0 place-items-center rounded-full font-display text-lg font-extrabold text-white">
                      {score}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate font-bold">{role}</p>
                      <p className="text-sm text-black/45">{company}</p>
                    </div>
                    <ArrowRight
                      className="ml-auto transition group-hover:translate-x-1"
                      size={18}
                    />
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between bg-ink px-4 py-3 text-white">
                <span className="text-sm font-semibold">3 strong matches today</span>
                <Sparkles size={17} />
              </div>
            </div>
            <div className="absolute -bottom-7 -left-5 -rotate-3 border border-black bg-white px-4 py-3 text-sm font-bold shadow-[4px_4px_0_#111]">
              No endless tabs ✦
            </div>
          </div>
        </div>
      </section>
      <div className="marquee border-b border-ink bg-ink py-3 text-white" aria-hidden="true">
        <div>
          DISCOVER BETTER • SCORE SMARTER • APPLY WITH INTENT • BUILD YOUR FUTURE • DISCOVER BETTER
          • SCORE SMARTER • APPLY WITH INTENT •
        </div>
      </div>
      <section id="how-it-works" className="scroll-mt-24 border-b border-black/10 bg-white py-24">
        <div className="mx-auto max-w-[1300px] px-5 lg:px-10">
          <div className="mb-14 flex flex-col justify-between gap-5 md:flex-row md:items-end">
            <div>
              <p className="eyebrow mb-4 w-fit">A clearer process</p>
              <h2 className="section-title">From résumé to shortlist.</h2>
            </div>
            <p className="max-w-md text-black/55">
              Three focused steps replace spreadsheet chaos and help you spend time on roles that
              deserve it.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {steps.map(([Icon, number, title, copy]: any) => (
              <article className="feature-card" key={number}>
                <div className="flex items-start justify-between">
                  <Icon size={30} />
                  <span className="font-display text-5xl font-extrabold text-black/10">
                    {number}
                  </span>
                </div>
                <h3 className="mt-12 font-display text-2xl font-bold tracking-tight">{title}</h3>
                <p className="mt-3 leading-7 text-black/55">{copy}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
      <section id="about" className="scroll-mt-24 bg-paper py-24">
        <div className="mx-auto grid max-w-[1300px] gap-10 px-5 lg:grid-cols-[.8fr_1.2fr] lg:px-10">
          <div>
            <p className="eyebrow mb-4 w-fit">Why Meridian</p>
            <h2 className="section-title">Career tools should feel human.</h2>
          </div>
          <div className="border-l-2 border-black pl-7 text-xl leading-9 text-black/65 sm:text-2xl">
            Built for students and early-career talent who want useful signals—not more noise.
            Meridian keeps applications manual, decisions yours, and the experience refreshingly
            clear.
            <button
              className="mt-8 flex items-center gap-2 text-base font-bold text-black"
              onClick={onLogin}
            >
              Login to your workspace <ArrowRight size={18} />
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}

function LoginDialog({
  onClose,
  onLogin,
}: {
  onClose: () => void;
  onLogin: (email: string) => void;
}) {
  const [value, setValue] = useState('');
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (event.currentTarget.reportValidity()) onLogin(value.trim());
  };
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        className="w-full max-w-md border-2 border-black bg-white p-7 shadow-[10px_10px_0_rgba(255,255,255,.35)]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-title"
      >
        <div className="flex items-start justify-between">
          <img src="/favicon-512.png" alt="Meridian" className="h-12 w-12 rounded-xl" />
          <button className="icon-button" onClick={onClose} aria-label="Close login">
            <X size={19} />
          </button>
        </div>
        <h2 id="login-title" className="mt-8 font-display text-3xl font-extrabold tracking-tight">
          Welcome to Meridian.
        </h2>
        <p className="mt-2 text-sm leading-6 text-black/55">
          Enter any valid email to continue. Verification will be added in a future release.
        </p>
        <form className="mt-7" onSubmit={submit}>
          <label className="text-sm font-bold" htmlFor="login-email">
            Email address
          </label>
          <div className="field mt-2 flex items-center gap-2">
            <Mail size={17} className="text-black/40" />
            <input
              id="login-email"
              className="min-w-0 flex-1 bg-transparent outline-none"
              type="email"
              placeholder="you@example.com"
              autoFocus
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          <button className="btn btn-dark mt-4 w-full py-3" type="submit">
            Continue <ArrowRight size={17} />
          </button>
        </form>
        <p className="mt-5 flex items-center gap-2 text-xs text-black/40">
          <ShieldCheck size={15} /> Prototype login · stored only in this browser
        </p>
      </section>
    </div>
  );
}

function Footer({ onAbout }: { onAbout: () => void }) {
  return (
    <footer className="border-t border-white/15 bg-ink text-white">
      <div className="mx-auto grid max-w-[1500px] gap-10 px-5 py-14 md:grid-cols-[1fr_auto] lg:px-10">
        <div>
          <div className="flex items-center gap-3">
            <img src="/favicon-512.png" alt="" className="h-11 w-11 rounded-xl bg-white" />
            <span className="font-display text-2xl font-extrabold tracking-tight">Meridian</span>
          </div>
          <p className="mt-4 max-w-sm text-sm leading-6 text-white/55">
            A calmer, smarter way for students to navigate the job search and find work that fits.
          </p>
        </div>
        <nav
          className="flex items-center gap-5 whitespace-nowrap text-sm sm:gap-8"
          aria-label="Footer navigation"
        >
          <a
            href="#about"
            onClick={(event) => {
              event.preventDefault();
              onAbout();
            }}
          >
            About
          </a>
          <a href="mailto:hello@meridian.app">Contact</a>
          <a id="terms" href="#terms">
            Terms
          </a>
          <a id="privacy" href="#privacy">
            Privacy
          </a>
        </nav>
      </div>
      <div className="border-t border-white/15 px-5 py-5 text-center text-xs text-white/45">
        © 2026 Meridian. All rights reserved.
      </div>
    </footer>
  );
}
