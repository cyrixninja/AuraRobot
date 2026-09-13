# Aura — AI Social Assistance Robot

## One-line pitch

Aura is a warm, expressive companion robot that uses Gemini Live Flash and Arduino UNO Q to turn ordinary voice conversation into safe, visible, physical interaction.

## Contest introduction

Aura explores how AI can feel less like a command line and more like a friendly presence. Built on Arduino UNO Q and Arduino App Lab, it listens to a user through Gemini Live, replies in a short natural voice, and shows personality through animated OLED eyes, a moving head, blinks, moods, and carefully bounded movement.

The project addresses the **Social Assistance Robot** scenario by making interaction approachable and low pressure. A user can greet Aura, ask a question, share an idea, or give a simple instruction without learning a mobile app or technical interface. Aura responds with both voice and body language: it looks around when curious, becomes happy during praise or celebration, and offers a gentle greeting when someone says hello. These small cues make the interaction clearer, more engaging, and more inviting for homes, classrooms, maker spaces, and wellbeing-oriented experiences.

Arduino UNO Q is central to the design. Its Linux side runs the Python voice application, Gemini Live connection, audio handling, and tool-call orchestration. Its microcontroller side handles deterministic L298N motor control, servo movement, OLED animation, and a timed movement deadline. Arduino Bridge and App Lab connect those two worlds, while the App Lab WebUI provides a transparent manual dashboard for setup, testing, and fallback control.

Aura is deliberately not presented as an autonomous robot. The current prototype has no camera, obstacle sensor, bumper, or cliff sensor, so it moves only after a clear direct instruction. Normal movements run at speed 70 for 2.5 seconds and are stopped by the MCU even if the voice process disconnects. Continuous movement is explicitly requested and implemented through short renewed timed pulses. This safety-first design keeps the prototype honest while leaving a clear path to future sensor-assisted mobility.

The project is modular and scalable. Its semantic action layer gives the language model safe actions such as `look`, `express`, `move_briefly`, and `stop_robot`, rather than unrestricted hardware access. Future versions can add opt-in vision, distance and cliff sensing, wake-word detection, local models, routines, multilingual modes, and accessibility-focused interaction flows without replacing the core architecture.

Aura demonstrates that a powerful AI robot does not have to be intimidating. With UNO Q and App Lab, it becomes possible to combine real-time embedded reliability with expressive conversational AI in a repairable, approachable companion platform.