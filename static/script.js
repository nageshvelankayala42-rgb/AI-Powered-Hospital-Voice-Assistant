const doctorSelect = document.querySelector("#doctorSelect");
const recommendBtn = document.querySelector("#recommendBtn");
const recommendationBox = document.querySelector("#recommendation");
const appointmentForm = document.querySelector("#appointmentForm");
const formMessage = document.querySelector("#formMessage");
const chatLog = document.querySelector("#chatLog");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const voiceBtn = document.querySelector("#voiceBtn");
const muteBtn = document.querySelector("#muteBtn");
const voiceStatus = document.querySelector("#voiceStatus");
const symptomsInput = document.querySelector("#symptoms");
const appointmentDateInput = document.querySelector("[name='appointment_date']");
const timeSlotSelect = document.querySelector("#timeSlotSelect");
let doctorsCache = [];
let voiceMuted = false;
let recognition = null;
let isListening = false;
let callActive = false;
let processingVoice = false;

function addChatMessage(text, type = "bot") {
    const message = document.createElement("div");
    message.className = type === "user" ? "user-message" : "bot-message";
    message.textContent = text;
    chatLog.appendChild(message);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function escapeHtml(value) {
    return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function renderList(items) {
    if (!Array.isArray(items) || items.length === 0) {
        return "";
    }
    return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function setFormMessage(text, type) {
    formMessage.textContent = text;
    formMessage.className = `message ${type}`;
}

function speak(text, afterSpeak = null) {
    if (voiceMuted || !("speechSynthesis" in window)) {
        if (afterSpeak) {
            afterSpeak();
        }
        return;
    }

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.95;
    utterance.pitch = 1;
    utterance.lang = "en-IN";
    utterance.onend = () => {
        if (afterSpeak) {
            afterSpeak();
        }
    };
    window.speechSynthesis.speak(utterance);
}

function updateVoiceStatus(text) {
    voiceStatus.textContent = text;
}

function listenAgainAfterReply() {
    if (!callActive || !recognition || isListening) {
        return;
    }

    setTimeout(() => {
        if (callActive && recognition && !isListening) {
            recognition.start();
        }
    }, 500);
}

function getFormData() {
    return Object.fromEntries(new FormData(appointmentForm).entries());
}

function getSelectedDoctorName() {
    const selectedOption = doctorSelect.options[doctorSelect.selectedIndex];
    return selectedOption ? selectedOption.textContent.trim() : "";
}

function missingBookingFields(data) {
    const required = [
        "patient_name",
        "patient_email",
        "patient_phone",
        "symptoms",
        "appointment_date",
        "appointment_time",
        "doctor_id",
    ];
    return required.filter((field) => !String(data[field] || "").trim());
}

async function loadSlots(preferredTime = "") {
    const doctorId = doctorSelect.value;
    const appointmentDate = appointmentDateInput.value;

    if (!doctorId || !appointmentDate) {
        timeSlotSelect.innerHTML = '<option value="">Select doctor and date first</option>';
        return [];
    }

    const response = await fetch(
        `/api/availability?doctor_id=${encodeURIComponent(doctorId)}&date=${encodeURIComponent(appointmentDate)}`
    );
    const result = await response.json();

    if (!response.ok) {
        timeSlotSelect.innerHTML = '<option value="">No slots available</option>';
        throw new Error(result.error || "Could not load slots.");
    }

    if (result.slots.length === 0) {
        timeSlotSelect.innerHTML = '<option value="">No slots available</option>';
        return [];
    }

    timeSlotSelect.innerHTML = result.slots
        .map((slot) => `<option value="${escapeHtml(slot)}">${escapeHtml(slot)}</option>`)
        .join("");

    if (preferredTime && result.slots.includes(preferredTime)) {
        timeSlotSelect.value = preferredTime;
    }

    return result.slots;
}

async function bookAppointment(data, spoken = false) {
    const missing = missingBookingFields(data);
    if (missing.length > 0) {
        const reply = `I still need ${missing.join(", ")} before I can book it.`;
        setFormMessage(reply, "error");
        addChatMessage(reply);
        speak(reply);
        return;
    }

    try {
        const response = await fetch("/api/appointments", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Booking failed.");
        }

        setFormMessage(`Appointment booked. ID: ${result.appointment_id}`, "success");
        const reply = spoken
            ? `Done. I booked your appointment. Your appointment ID is ${result.appointment_id}.`
            : "Your appointment has been booked successfully.";
        addChatMessage(reply);
        speak(reply);
        appointmentForm.reset();
        recommendationBox.classList.add("hidden");
        await loadDoctors();
        await loadSlots();
    } catch (error) {
        setFormMessage(error.message, "error");
        addChatMessage(error.message);
        speak(error.message);
    }
}

async function sendAssistantMessage(message) {
    const formData = getFormData();
    addChatMessage(message, "user");

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message,
                ...formData,
                selected_doctor: getSelectedDoctorName(),
            }),
        });
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Assistant failed.");
        }

        if (result.suggested_doctor_id) {
            const exists = doctorsCache.some(
                (doctor) => doctor.id === Number(result.suggested_doctor_id)
            );
            if (exists) {
                doctorSelect.value = result.suggested_doctor_id;
            }
        }

        if (result.requested_date) {
            appointmentDateInput.value = result.requested_date;
        }

        let slots = [];
        if (doctorSelect.value && appointmentDateInput.value) {
            slots = await loadSlots(result.requested_time || "");
        }

        if (result.requested_time && slots.includes(result.requested_time)) {
            timeSlotSelect.value = result.requested_time;
        }

        const reply = `${result.reply} ${result.next_action || ""}`.trim();
        addChatMessage(reply);
        speak(reply, listenAgainAfterReply);

        if (result.should_book) {
            const latestData = getFormData();
            await bookAppointment(latestData, true);
        }
    } catch (error) {
        const reply = "Sorry, I could not answer that right now. Please try again.";
        addChatMessage(reply);
        speak(reply, listenAgainAfterReply);
    }
}

async function loadDoctors() {
    const response = await fetch("/api/doctors");
    const doctors = await response.json();
    doctorsCache = doctors;

    doctorSelect.innerHTML = doctors
        .map((doctor) => (
            `<option value="${doctor.id}">
                ${doctor.name} - ${doctor.specialty}
            </option>`
        ))
        .join("");
    await loadSlots();
}

document.querySelectorAll("[data-symptom]").forEach((button) => {
    button.addEventListener("click", () => {
        symptomsInput.value = button.dataset.symptom;
        addChatMessage(`Symptoms noted: ${button.dataset.symptom}`, "user");
        speak(`Symptoms noted: ${button.dataset.symptom}`);
    });
});

recommendBtn.addEventListener("click", async () => {
    const symptoms = symptomsInput.value.trim();
    if (!symptoms) {
        setFormMessage("Please enter symptoms first.", "error");
        return;
    }

    recommendBtn.disabled = true;
    recommendBtn.textContent = "Checking...";
    recommendationBox.classList.add("hidden");
    addChatMessage(symptoms, "user");

    try {
        const response = await fetch("/api/recommend", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symptoms }),
        });
        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Recommendation failed.");
        }

        doctorSelect.value = result.doctor.id;
        const urgencyClass = result.urgency === "emergency" ? "urgent" : "normal";
        recommendationBox.innerHTML = `
            <strong>${escapeHtml(result.doctor.name)}</strong><br>
            Specialty: ${escapeHtml(result.specialty)}<br>
            Confidence: ${escapeHtml(result.confidence || "medium")}<br>
            <span class="${urgencyClass}">Urgency: ${escapeHtml(result.urgency || "routine")}</span><br>
            ${escapeHtml(result.reason)}
            ${renderList(result.questions_to_ask)}
            ${renderList(result.preparation_tips)}
            <small>${escapeHtml(result.safety_note)}</small>
        `;
        recommendationBox.classList.remove("hidden");
        addChatMessage(
            `I recommend ${result.doctor.name}, ${result.specialty}. ` +
            `${result.reason} Next, choose a date and book the appointment.`
        );
        speak(
            `I recommend ${result.doctor.name}, ${result.specialty}. ` +
            `${result.reason} Next, choose a date and book the appointment.`
        );
        setFormMessage("", "");
    } catch (error) {
        setFormMessage(error.message, "error");
        const reply = "Sorry, I could not generate a recommendation right now.";
        addChatMessage(reply);
        speak(reply);
    } finally {
        recommendBtn.disabled = false;
        recommendBtn.textContent = "Get AI Recommendation";
    }
});

chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message) {
        return;
    }

    chatInput.value = "";
    await sendAssistantMessage(message);
});

function setupVoiceAgent() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
        voiceBtn.disabled = true;
        updateVoiceStatus("Voice input is not supported in this browser. Use Chrome or Edge.");
        return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = "en-IN";
    recognition.interimResults = false;
    recognition.continuous = false;

    recognition.addEventListener("start", () => {
        isListening = true;
        voiceBtn.textContent = "End Call";
        updateVoiceStatus("Listening. Speak your symptoms or booking question.");
    });

    recognition.addEventListener("result", async (event) => {
        const transcript = event.results[0][0].transcript.trim();
        chatInput.value = transcript;
        updateVoiceStatus(`Heard: ${transcript}`);
        processingVoice = true;
        await sendAssistantMessage(transcript);
        processingVoice = false;
    });

    recognition.addEventListener("error", (event) => {
        updateVoiceStatus(`Voice error: ${event.error}. Try again.`);
        processingVoice = false;
    });

    recognition.addEventListener("end", () => {
        isListening = false;
        voiceBtn.textContent = callActive ? "End Call" : "Start Call";
        if (callActive && !processingVoice) {
            listenAgainAfterReply();
        }
    });
}

voiceBtn.addEventListener("click", () => {
    if (!recognition) {
        return;
    }

    if (callActive) {
        callActive = false;
        recognition.stop();
        voiceBtn.textContent = "Start Call";
        updateVoiceStatus("Voice call ended.");
        return;
    }

    callActive = true;
    voiceBtn.textContent = "End Call";
    updateVoiceStatus("Voice call started.");
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    recognition.start();
});

muteBtn.addEventListener("click", () => {
    voiceMuted = !voiceMuted;
    muteBtn.textContent = voiceMuted ? "Voice Off" : "Voice On";
    muteBtn.setAttribute("aria-pressed", String(voiceMuted));
    if (voiceMuted) {
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }
        updateVoiceStatus("Voice replies muted.");
    } else {
        updateVoiceStatus("Voice replies enabled.");
    }
});

appointmentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await bookAppointment(getFormData());
});

doctorSelect.addEventListener("change", () => {
    loadSlots().catch((error) => setFormMessage(error.message, "error"));
});

appointmentDateInput.addEventListener("change", () => {
    loadSlots().catch((error) => setFormMessage(error.message, "error"));
});

loadDoctors().catch(() => {
    setFormMessage("Could not load doctors. Check Flask and MySQL connection.", "error");
});
setupVoiceAgent();
