# CLAUDE.md — Quanttum AI

## 1. Project identity

Quanttum AI is an offline-first personal AI and computer assistant for Windows.

It is not merely a chatbot. Its purpose is to understand, remember, assist, act and improve the way its owner interacts with technology.

The long-term vision includes:
- Natural language understanding and contextual reasoning.
- Persistent, searchable memory.
- Local file and document management.
- Computer interaction and task automation.
- Voice input and output.
- System monitoring and management.
- Optional integration with local AI models.
- Personalised assistance that improves through use.

## 2. Development environment

- Operating system: Windows 10, 64-bit.
- Python: 3.13.
- RAM: 4 GB.
- Processor: Intel Celeron N4020, 2 cores.
- Storage: SSD.
- Interface: CustomTkinter.
- Database: SQLite.
- Version control: Git and GitHub.

All implementations must consider these hardware limitations.

Avoid unnecessary dependencies, background processes and memory-intensive operations.

## 3. Architecture

Develop Quanttum AI as a modular system with five principal components.

### Quanttum Core
Central orchestration, command routing, module coordination and session management.

### Intelligence Engine
Intent recognition, natural language processing, contextual reasoning and optional local language models.

### Memory Engine
Persistent memory, conversation history, knowledge storage, retrieval and contextual recall.

### Action Engine
Application launching, file operations, computer interaction, task execution and automation.

### Perception and System Engine
Voice processing, document input, system monitoring, resource management and environmental awareness.

Modules must remain independent wherever practical.

## 4. Engineering principles

1. Offline-first operation.
2. Modular architecture.
3. Low-resource execution.
4. Persistent contextual memory.
5. User control and permission-based actions.
6. Reliability and recoverability.
7. Minimal external dependencies.
8. Clear separation between intelligence and execution.
9. Local data privacy.
10. Extensibility.

The assistant must remain useful when no language model is installed or available.

An LLM must never become a single point of failure.

## 5. Security and permissions

Classify computer actions into three levels:

- Level 1: Read-only operations.
- Level 2: Reversible operations requiring appropriate confirmation.
- Level 3: Sensitive, destructive or high-impact operations requiring explicit authorisation.

Record significant actions in an audit log.

Never execute arbitrary commands simply because an LLM generated them.

Never expose passwords, tokens, private databases or personal information.

## 6. Development rules

- Inspect existing code before modifying it.
- Preserve working functionality.
- Explain significant architectural changes.
- Test changes before considering them complete.
- Avoid unnecessary rewrites.
- Keep modules small and maintainable.
- Document new dependencies.
- Respect the hardware constraints.
- Never overwrite user data without permission.
- Use Git checkpoints before major changes.

## 7. Current development status

Version: 0.1.1

Implemented:
- CustomTkinter graphical interface.
- Basic intent recognition.
- SQLite conversation persistence.
- Basic date and time responses.
- RAM monitoring.
- Explicit long-term memory (remember, keyword recall, forget by ID) in the `memories` table.
- Versioned SQLite schema (`PRAGMA user_version`) and a stdlib `unittest` suite in `tests/`.

Not yet implemented:
- Advanced contextual intelligence.
- Semantic and structured long-term memory.
- Computer action execution.
- Voice interaction.
- Local LLM integration.
- Advanced automation.

## 8. Collaboration

The project owner makes final architectural and development decisions.

Claude assists with repository inspection, implementation, refactoring and testing.

ChatGPT assists with architecture, technical planning, integration and development coordination.

Explain code changes so the project owner can understand, test and maintain the software.

## 9. Immediate objective

Develop Quanttum AI 0.2.

Prioritise:
1. A stronger intelligence and command-routing system.
2. Structured persistent memory.
3. Contextual understanding of previous interactions.
4. Reliable module communication.
5. Tests for existing and new functionality.

Do not attempt to implement every long-term feature at once.

Build a stable foundation that can grow into the complete Quanttum AI vision.