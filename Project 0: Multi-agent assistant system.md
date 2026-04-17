
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



