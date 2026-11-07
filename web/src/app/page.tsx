import Link from "next/link";
import { Icon } from "@/components/Icons";
import { HeroForm, LandingNav } from "@/components/Landing";

// Numbers below come from the three sample roadmaps in docs/samples/.
const SAMPLES = [
  { who: "Ravi · B.Tech CSE · 3rd year", role: "DevOps Engineer", weeks: 30, hours: 300, from: 26, to: 72 },
  { who: "Priya · B.Com · 3rd year", role: "Data Analyst", weeks: 31, hours: 242, from: 19, to: 71 },
  { who: "Arjun · B.Tech CSE · 4th year", role: "ML Engineer", weeks: 28, hours: 330, from: 30, to: 73 },
];

export default function Home() {
  return (
    <div>
      <LandingNav />
      <div className="wrapx">
        <section className="hero">
          <span className="pill">
            <b>SIH 2025</b>Built on O*NET 31.0 job data
          </span>
          <h1>
            Your career, mapped from <em>where you are</em>
          </h1>
          <p>
            Tell CareerCompass what you study and what you already know. It plans the courses and projects that take you
            to the job you want, week by week.
          </p>
          <HeroForm />
          <div className="hnote">Describe yourself in your own words · Takes under a minute</div>

          <div className="shot" aria-hidden="true">
            <div className="bar">
              <i />
              <i />
              <i />
            </div>
            <div className="in">
              <div className="sb">
                <div className="nr">
                  <span>+</span>New chat
                </div>
                <small>Recents</small>
                <div className="it on">DevOps Engineer roadmap</div>
                <div className="it">Data Analyst roadmap</div>
                <div className="it">Machine Learning Engineer roadmap</div>
              </div>
              <div className="th">
                <div className="u">
                  3rd year CSE, I know Linux, Git and some Python. I want to be a DevOps engineer. I have 10 hours a
                  week.
                </div>
                <div style={{ fontFamily: "var(--serif)", fontSize: 15 }}>
                  Your roadmap is ready. At <b>10 hours a week</b> it takes <b>30 weeks</b> and moves your job readiness
                  from <b>26% to 72%</b>.
                </div>
                <div className="card">
                  <i />
                  <div>
                    <b style={{ fontWeight: 500 }}>DevOps Engineer roadmap</b>
                    <br />
                    <span style={{ color: "var(--muted)", fontSize: 12 }}>5 phases · 4 projects</span>
                  </div>
                </div>
              </div>
              <div className="pn">
                <h4>DevOps Engineer roadmap</h4>
                {[
                  ["Containerized 3-tier app", "Weeks 1–4 · 40 h"],
                  ["CI pipeline with tests and image scan", "Weeks 5–10 · 57 h"],
                  ["AWS environment as code", "Weeks 10–20 · 102 h"],
                  ["GitOps Kubernetes with monitoring", "Weeks 20–29 · 91 h"],
                ].map(([t, s]) => (
                  <div className="ph" key={t} style={{ ["--c" as string]: "var(--accent)" }}>
                    <b />
                    <div>
                      {t}
                      <small>{s}</small>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="blk" id="how">
          <div className="eyebrow">How it works</div>
          <h2>A short conversation to a plan you can follow</h2>
          <p className="sub">
            CareerCompass is a conversation, not a form. It asks only for what it could not work out from your message.
          </p>
          <div className="steps">
            <div className="step">
              <div className="n">1</div>
              <h3>Describe yourself</h3>
              <p>Your branch, year, what you know and the role you want, in your own words.</p>
            </div>
            <div className="step">
              <div className="n">2</div>
              <h3>Answer what&apos;s missing</h3>
              <p>Usually one question, like how many hours a week you can study.</p>
            </div>
            <div className="step">
              <div className="n">3</div>
              <h3>Follow your roadmap</h3>
              <p>Phases of courses and projects, ordered by prerequisites, sized to your time.</p>
            </div>
          </div>
        </section>

        <section className="blk" id="features">
          <div className="eyebrow">Why it works</div>
          <h2>Plans built on what employers actually ask for</h2>
          <p className="sub">
            Every roadmap is matched against the U.S. Department of Labor&apos;s O*NET database of 1,016 occupations,
            with their skills, knowledge and tools.
          </p>
          <div className="feat">
            <div className="f wide">
              <div>
                <div className="ic">
                  <Icon name="chart" />
                </div>
                <h3>Grounded in real job data</h3>
                <p>
                  &quot;DevOps engineer&quot; is mapped to the O*NET occupations it overlaps with, and your gaps are
                  scored against them.
                </p>
              </div>
              <div className="onet">
                {[
                  ["Computer Systems Engineers/Architects", 45],
                  ["Network & Computer Systems Admins", 30],
                  ["Software Developers", 25],
                ].map(([t, a]) => (
                  <div key={t}>
                    <div>
                      {t}
                      <span>
                        <i style={{ width: `${a}%` }} />
                      </span>
                    </div>
                    {a}%
                  </div>
                ))}
              </div>
            </div>
            <div className="f">
              <div className="ic">
                <Icon name="clock" />
              </div>
              <h3>Fits your week</h3>
              <p>
                A feasibility check makes sure the plan matches the hours you have, and tells you what to change if it
                doesn&apos;t.
              </p>
            </div>
            <div className="f">
              <div className="ic">
                <Icon name="wrench" />
              </div>
              <h3>Projects you can show</h3>
              <p>Each phase ends with a portfolio project, with clear deliverables for your GitHub and interviews.</p>
            </div>
            <div className="f">
              <div className="ic">
                <Icon name="check" />
              </div>
              <h3>Skips what you know</h3>
              <p>Already comfortable with Linux and Git? Those topics are left out so you don&apos;t waste weeks.</p>
            </div>
            <div className="f">
              <div className="ic">
                <Icon name="globe" />
              </div>
              <h3>Telugu support coming</h3>
              <p>Chat and read your roadmap in the language you&apos;re most comfortable with.</p>
            </div>
          </div>
        </section>

        <section className="blk" id="samples">
          <div className="eyebrow">Sample roadmaps</div>
          <h2>See what a plan looks like</h2>
          <p className="sub">Three real outputs from the planner, for three different students.</p>
          <div className="samples">
            {SAMPLES.map((s) => (
              <Link key={s.role} className="sm" href="/login" style={{ textDecoration: "none" }}>
                <div className="who">{s.who}</div>
                <h3>{s.role}</h3>
                <div className="nums">
                  <div>
                    <b>{s.weeks}</b>
                    <small>weeks</small>
                  </div>
                  <div>
                    <b>{s.hours} h</b>
                    <small>total</small>
                  </div>
                  <div>
                    <b>
                      {s.from}→{s.to}%
                    </b>
                    <small>readiness</small>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </section>

        <section className="cta" id="colleges">
          <h2>Start your roadmap in under a minute</h2>
          <p>Free for students. Colleges can use it for placement-cell career guidance.</p>
          <Link className="btn" href="/login">
            Try CareerCompass
          </Link>
        </section>
        <footer>
          <span>© 2026 CareerCompass · SIH25094</span>
          <span>Job data: O*NET 31.0 Database, CC BY 4.0</span>
        </footer>
      </div>
    </div>
  );
}
