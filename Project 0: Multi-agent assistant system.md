
# Project 0: Multi-agent assistant system

## 1. Purpose

This document defines the architecture of a five-agent AI system designed for long-term personal assistance, structured collaboration, and controlled agent evolution.

The project is intended to support two parallel modes of use:

- **daily interaction through a chat interface** for lightweight communication, instructions, reminders, and content forwarding
- **a dedicated management interface** for approvals, configuration, supervision, and system inspection

The design goals are:

- strong separation of responsibilities across agents
- coordinated multi-agent collaboration without role confusion
- private memory boundaries for each agent where needed
- selective shared context for efficient cooperation
- support for supervision, scoring, and future improvement
- compatibility with a custom WebUI for system control
- compatibility with a chat-based frontend for everyday use

---

## 2. System Overview

The system is structured as a unified backend with two user-facing surfaces:

- **Chat Frontend**: the daily interaction layer for communicating with agents in private or group chat, forwarding content, receiving reminders, and issuing lightweight instructions
- **WebUI Control Panel**: the management layer for configuration, approvals, monitoring, memory review, audit access, and agent tuning

Both interfaces connect to the same backend system, which consists of:

- **Orchestrator Runtime**: the central coordination layer responsible for routing tasks, managing agent cooperation, and handling time-sensitive events
- **Five Specialized Agents**:
  - Manager Agent
  - Secretary Agent
  - Intelligence Agent
  - Learning Agent
  - Supervisor Agent
- **Memory and Data Layer** for user profile, shared state, private agent memory, knowledge storage, source cache, and audit records
- **Tool Gateway** for search, APIs, content ingestion, messaging, scheduling, and other external integrations

### Core design rule

Each agent should keep its **own private working memory**. Shared information should pass through a **controlled collaboration layer** rather than unrestricted memory access.

A key governance rule is to **separate coordination authority from inspection authority**:

- the **Manager** is responsible for planning, scheduling, coordination, and structured data sharing
- the **Supervisor** is responsible for inspection, auditing, investigation, and evaluation

This separation keeps the system explainable, safer to operate, and easier to scale.

### Temporary Decisions:

This part is open for discussion and may change if you suggest or find anything more suitable.

Temporarily we decide to use Telegram as the chat frontend application. And develop a WebUI as control backend. We intend to use LangGraph as the basic framework and maybe in the future we can introduce Letta for improving long-term memory.

In the first iteration, the intellgence agent information source will be limited to only Twitter news and followers. An example link for testing is "https://x.com/business/status/2042407370320396457".
And in this phase, the learning agent learning source will be limited to only text or shared wechat article link. An example link for testing is "https://mp.weixin.qq.com/s/apkuuxHmC1c6bR0kWhgmUA"


## 3. Agent Responsibilities

## 3.1 Manager Agent

The Manager Agent is the planner, scheduler, and coordination authority.

### Main responsibilities
- receive user goals, plans, priorities, and upcoming appointments
- optimize plans and identify missing work(with user's approve)
- coordinate other agents without acting as an inspector
- manage structured data sharing between agents through approved channels
- review work summaries and task states
- summon relevant agents to assist the user when a planned appointment or deadline is approaching
- propose changes, adjustments, priorities, and next actions(with user's approve)

### Notes
- it should **not** directly inspect other agents' private memory, raw conversations, or detailed traces
- it should primarily read summarized reports and structured task states
- it acts as an orchestrator and coordination authority, not as an investigative authority

---

## 3.2 Secretary Agent

The Secretary Agent is the emotional support and rhythm-maintenance agent.

### Main responsibilities
- observe the happening user conversation with other agents
- sometimes provide encouragement and positive reinforcement while user talking to other agents
- reminds user when appointment is approaching
- proactively maintain a playful, warm, teasing, and charming style from time to time, to maintain user momentum and emotional stability
- convert work updates into human-friendly support messages

### Notes
- it should only see agent conversation happening, and direction from manager agent.
- it should not read sensitive logs, technical traces, or raw diagnostic material

---

## 3.3 Intelligence Agent

The Intelligence Agent is the external information collection and briefing agent.

### Main responsibilities
- collect news and updates from external sources(currently only from twitter)
- monitor selected platforms and information channels
- generate daily or thematic briefings
- recommend new sources, accounts, or topics to follow

### Notes
- it should perform filtering, de-duplication, tagging, and source assessment
- it should not write directly into the formal knowledge base without review

---

## 3.4 Learning Agent

The Learning Agent is the long-term knowledge processing and review agent.

### Main responsibilities
- maintain notes and the knowledge base
- process articles, links, transcripts, and AI conversation extracts
- distill useful knowledge into structured entries
- build review schedules based on user's memory curve.
- trace sources and preserve provenance
- produce concise knowledge points from large inputs

### Notes
- this agent manages long-term knowledge assets
- it should focus on durable, structured knowledge rather than temporary work noise

---

## 3.5 Supervisor Agent

The Supervisor Agent is the audit, evaluation, and improvement agent.

### Main responsibilities
- monitor runtime behavior of all agents
- detect failures, drift, inefficiency, or repeated errors
- investigate incidents and perform detailed inspection when needed and when user asks
- score agent performance
- recommend improvements to prompts, tools, routing, or future fine-tuning

### Notes
- it is the primary inspection and audit authority in the system
- it should recommend changes, not silently self-modify production behavior
- configuration changes should go through approval and version control

---

## 4. Refined Backend Design

## 4.1 Design Implications of Section 3

The updated agent responsibilities imply five major system rules:

1. **Manager authority is coordination-only**
   - The Manager can schedule, route, summarize, and request cooperation.
   - The Manager cannot inspect private internals of other agents.

2. **Supervisor authority is inspection-only by default**
   - The Supervisor handles audits, deep inspection, scoring, and incident investigation.
   - The Supervisor should not directly modify production settings.

3. **Secretary visibility is narrow and live-context-oriented**
   - The Secretary mainly sees current user-facing conversation events and Manager direction.
   - The Secretary should not see audit, diagnostics, or hidden private memory.

4. **Intelligence is source-focused and write-restricted**
   - The Intelligence Agent collects and briefs.
   - It should not directly write into the formal knowledge base.

5. **Learning owns durable knowledge**
   - The Learning Agent is the main writer to the formal knowledge base.
   - It should be the gatekeeper for durable notes, provenance, and review scheduling.

---

## 4.2 Runtime Topology

The recommended runtime topology is:

- **Frontend Control Panel**
- **Orchestrator Runtime**
- **Agent Execution Layer**
- **Memory and Storage Layer**
- **Tool Gateway**
- **Audit and Metrics Layer**

### Frontend Control Panel
The frontend should manage:
- user goals and plans
- appointment inputs
- agent configuration
- manual approvals
- trace viewing
- score dashboards
- prompt version review
- memory browsing with permission filters

### Orchestrator Runtime
The runtime should handle:
- task routing
- event dispatch
- appointment-based triggers
- state transitions
- retry and timeout policies
- structured inter-agent message passing

### Agent Execution Layer
Each agent should execute as an isolated worker with:
- private memory scope
- role-specific tool access
- configurable model/prompt profile
- controlled access to shared layers

### Memory and Storage Layer
The memory layer should be separated into:
- user profile
- shared blackboard
- agent private memory
- knowledge base
- source cache
- audit logs and scores

### Tool Gateway
The tool layer should expose only permissioned tools per agent.
Examples:
- Twitter/X collection
- web fetch and metadata parser
- note ingestion
- transcript ingestion
- calendar or appointment feed
- summarization tools
- provenance extraction
- scoring and metrics APIs

### Audit and Metrics Layer
A dedicated observability subsystem should record:
- runtime events
- failures
- output quality scores
- latency and cost metrics
- incident records
- prompt/model revision suggestions

---

## 5. Refined Memory Model

## 5.1 Layer A: User Profile and Long-Term Preferences

### Purpose
A shared long-term representation of the user.

### Typical contents
- long-term goals
- planning preferences
- reminder preferences
- preferred output style
- work rhythm preferences
- interests and topic priorities
- learning and review preferences

### Access
- Manager: read/write
- Secretary: partial read
- Intelligence: partial read
- Learning: read
- Supervisor: read

### Refinement
Because the Manager requires user approval before optimization or major proposal changes, this layer should also record:
- approval preferences
- decision authority rules
- what kinds of plan changes need explicit confirmation

---

## 5.2 Layer B: Shared Blackboard / Work Report Layer

### Purpose
The main collaboration surface between agents.

### Typical contents
- task summaries
- progress updates
- upcoming deadlines
- appointment preparation status
- blocking issues
- requests for cooperation
- approved next-step suggestions
- handoff summaries between agents

### Access
- Manager: read/write
- Secretary: simplified read
- Intelligence: read/write
- Learning: read/write
- Supervisor: read/write

### Refinement
This layer should be the main way the Manager coordinates cross-agent cooperation.
It should also support appointment-triggered aggregation such as:
- "meeting preparation package ready"
- "deadline support requested"
- "briefing assembled"
- "review reminders generated"

---

## 5.3 Layer C: Agent Private Working Memory

### Purpose
Temporary, agent-specific working state.

### Typical contents
- current thread state
- internal drafts
- local work hypotheses
- temporary notes
- unresolved partial outputs
- role-specific local context

### Access
Each agent should have its own private version.

### Refinement
This layer must remain isolated.
The Manager must not inspect it.
The Supervisor may inspect it only when needed and when the user asks, or under a defined incident policy.

---

## 5.4 Layer D: Formal Knowledge Base / Learning Store

### Purpose
The durable knowledge asset managed mainly by the Learning Agent.

### Typical contents
- structured knowledge entries
- source-linked notes
- concise distilled insights
- concept maps
- review cards
- topic summaries
- extracted useful points from AI conversations

### Access
- Learning: read/write
- Manager: read
- Intelligence: candidate submission only
- Secretary: no direct access by default
- Supervisor: read-only

### Refinement
Only the Learning Agent should perform formal write operations here.
Other agents may:
- suggest candidate material
- request read access
- consume summaries
but should not directly create authoritative knowledge entries.

---

## 5.5 Layer E: Audit Logs, Traces, Scores, and Incident Reports

### Purpose
Operational oversight and diagnostics.

### Typical contents
- execution records
- tool usage history
- latency and cost metrics
- failures
- quality evaluations
- incident records
- score history
- supervisor recommendations

### Access
- Supervisor: full read/write
- Manager: summary read only
- User: visible through the control panel
- Other agents: no default access

### Refinement
The Manager should be able to see summary-level health information such as:
- "Intelligence degraded today"
- "Learning review backlog growing"
- "Secretary response frequency too high"
without being given raw inspection authority.

---

## 5.6 Layer F: Source Cache and External Material Store

### Purpose
Store raw or semi-processed material gathered from the outside world.

### Typical contents
- fetched tweets/posts
- metadata
- timestamps
- tags
- source reliability annotations
- de-duplication fingerprints
- clustering results
- cached media or text extracts

### Access
- Intelligence: read/write
- Learning: read
- Manager: summary read
- Supervisor: read
- Secretary: no access by default

### Refinement
Because Intelligence is currently limited to Twitter/X, this layer can be simplified at first:
- account watchlists
- collected post cache
- thread summaries
- engagement or relevance tags
- candidate follow recommendations

---

## 6. Refined Sharing Rules

## 6.1 What Should Be Shared

### Shared 1: User Profile
All agents benefit from understanding the user, with filtered access by role.

### Shared 2: Shared Blackboard
This remains the main collaboration surface and should be the default shared layer.

### Shared 3: Task and Appointment Status
Since the Manager is appointment-aware, time-sensitive status should be a first-class shared object.

### Shared 4: Read-Only Knowledge Outcomes
Manager and Supervisor may read durable knowledge results.
Intelligence may read coverage summaries to avoid redundancy.

### Shared 5: Health Summaries
The Manager may receive summary-level agent health signals from the Supervisor without direct inspection access.

---

## 6.2 What Must Be Isolated

### Isolated 1: Agent Private Working Memory
Each agent's local working memory must remain isolated.

### Isolated 2: Full Raw Conversations
These should not be openly visible to all agents.

### Isolated 3: Detailed Traces and Diagnostics
These belong primarily to the Supervisor and the user.

### Isolated 4: Sensitive Tool Outputs
Protected files, private messages, and privileged tool outputs should remain permissioned.

### Isolated 5: Configuration Drafts and Experimental Prompt Revisions
These should remain in governance-controlled storage rather than shared operational memory.

---

## 7. Refined Access Policy

| Memory Layer | Manager | Secretary | Intelligence | Learning | Supervisor |
|---|---|---|---|---|---|
| User Profile | R/W | R (partial) | R (partial) | R | R |
| Shared Blackboard | R/W | R (simplified live context) | R/W | R/W | R/W |
| Manager Private Memory | R/W | - | - | - | Approval-based inspection |
| Secretary Private Memory | - | R/W | - | - | Approval-based inspection |
| Intelligence Private Memory | - | - | R/W | - | Approval-based inspection |
| Learning Private Memory | - | - | - | R/W | Approval-based inspection |
| Supervisor Private Memory | - | - | - | - | R/W |
| Formal Knowledge Base | R | - | Candidate submission only | R/W | R |
| Source Cache | Summary | - | R/W | R | R |
| Audit Summary | Summary | - | - | - | R/W |
| Audit Detail | - | - | - | - | R/W |
| Prompt / Model Config | Controlled proposal only | - | - | - | Suggest / review only |

### Notes
- The Manager may propose coordination changes, scheduling changes, and cross-agent data-sharing rules, but not inspect private internals.
- The Supervisor may inspect and score, but should not directly apply production changes.
- The Secretary should receive only live conversation context and Manager direction relevant to support behavior.
- The Intelligence Agent should be prevented from writing directly into the formal knowledge base.
- The Learning Agent is the main durable-knowledge writer.

---

## 8. Refined Data Flow Design

## 8.1 Main Planning and Coordination Flow

1. The user provides a goal, plan, or appointment.
2. The Manager interprets priority, scope, dependencies, and time sensitivity.
3. The Manager routes work to one or more specialized agents.
4. If an appointment or deadline is approaching, the Manager may summon relevant agents to assist.
5. Specialized agents perform their work and write summarized outputs to the Shared Blackboard.
6. The Manager synthesizes the next approved recommendation, reminder, or coordination response.
7. The user receives the result and approves major plan changes where required.

---

## 8.2 Secretary Support Flow

1. The Secretary observes current user-facing conversation events and selected Manager direction.
2. It decides whether encouragement, support, or reminder behavior is appropriate.
3. It generates short support-oriented messages.
4. It should not consume audit logs, traces, or hidden private memory.
5. If an appointment is approaching, it may send a user-facing reminder aligned with Manager coordination.

### Design note
Secretary output should remain lightweight, supportive, and context-aware.
Its frequency should be rate-limited to avoid annoyance.

---

## 8.3 Intelligence Flow

1. The Intelligence Agent monitors Twitter/X sources.
2. It fetches candidate updates.
3. It cleans, tags, de-duplicates, and assesses source quality.
4. It produces:
   - daily or thematic briefings for the Manager
   - source/account/topic recommendations
   - candidate learning material for Learning review when relevant
5. It does not directly formalize knowledge.

### Design note
Because the current scope is Twitter/X only, ingestion rules can be stricter and simpler in the first version.

---

## 8.4 Learning Flow

1. Inputs arrive from the user, AI conversation extracts, articles, transcripts, or Intelligence recommendations.
2. The Learning Agent parses and distills the material.
3. It creates structured knowledge entries.
4. It records source provenance.
5. It generates review schedules based on the user's memory curve.
6. It posts summary-level updates to the Shared Blackboard when needed.

### Design note
Learning should be the main bridge between transient information and durable knowledge.

---

## 8.5 Supervision Flow

1. The Supervisor listens to runtime events, summaries, quality metrics, and failure signals.
2. It creates incident records when anomalies appear.
3. It performs deeper inspection when needed and when the user asks, or under a defined incident policy.
4. It scores agent performance.
5. It produces recommendations for prompts, tools, routing, or future fine-tuning.
6. Final changes go through user approval and versioned rollout.

### Design note
Supervisor authority should remain investigative and advisory, not executive.

---

## 9. Shared Memory Writing Rules

Only selected content should be written into shared layers.

### Allowed
- task summaries
- appointment-preparation summaries
- structured status updates
- approved next actions
- handoff notes
- requests for coordination
- review reminders
- durable knowledge summaries

### Not allowed by default
- full raw conversations
- chain-of-thought style scratch material
- large unfiltered tweet dumps
- noisy failed experiment logs
- private diagnostic traces
- unreviewed formal knowledge writes by non-Learning agents

### Principle
Shared memory should stay concise, durable, coordination-oriented, and low-noise.

---

## 10. Governance and Approval Model

## 10.1 Coordination vs Inspection

The architecture should preserve a strict separation:

- **Manager** = planning, scheduling, coordination, data-sharing control
- **Supervisor** = inspection, auditing, incident investigation, scoring

This separation prevents authority collapse and keeps the system explainable.

---

## 10.2 User Approval Boundaries

User approval should be required for:
- significant plan optimization changes
- reprioritization that changes the user's intended schedule
- major next-step changes proposed by Manager
- production prompt changes
- model changes
- fine-tuning rollout
- policy changes for data sharing or inspection

User approval should not be required for:
- routine summary generation
- reminder generation
- normal Intelligence briefing collection
- normal Learning review scheduling
- passive Supervisor observation

---

## 10.3 Configuration Governance

The recommended configuration flow is:

1. Supervisor recommends
2. Manager organizes context or routing implications where relevant
3. User reviews and approves
4. Configuration service versions and applies updates
5. Supervisor monitors impact after rollout

---

## 11. Architectural Checks and Refinements

### 11.1 Strong choices preserved
- clear separation of planning, support, intelligence, learning, and supervision
- Manager coordination without inspection authority
- Supervisor inspection without silent self-modification authority
- Learning as the durable knowledge writer
- Intelligence as a source-focused briefing system
- use of a shared blackboard rather than unrestricted memory sharing

### 11.2 Key constraints reinforced
- Secretary visibility is intentionally narrow
- Manager proposals require user approval for meaningful plan changes
- Intelligence is limited to Twitter/X at the current stage
- Supervisor deep inspection is conditional
- formal knowledge writing remains centralized in Learning

### 11.3 Main risks to avoid
- turning all agent memory into one shared pool
- giving Manager hidden inspection powers
- letting Supervisor directly rewrite production settings
- allowing Intelligence to bypass Learning and pollute the knowledge base
- letting Secretary consume hidden internal logs
- generating excessive Secretary interventions without rate limits

---

## 12. Final Recommended Structure

### Shared layers
- User Profile and Long-Term Preferences
- Shared Blackboard / Work Reports
- Task and Appointment Status
- Read-only Durable Knowledge Outcomes
- Summary-Level Agent Health Signals

### Isolated layers
- Agent Private Working Memory
- Full Raw Conversations
- Detailed Traces and Diagnostic Logs
- Sensitive Tool Outputs
- Prompt Drafts and Experimental Revision History

### Governance layer
- Supervisor inspects, audits, and recommends
- Manager organizes, schedules, and coordinates
- User approves
- Configuration service versions and applies updates



