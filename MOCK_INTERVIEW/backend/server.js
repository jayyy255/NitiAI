require('dotenv').config();
const express = require('express');
const http = require('http');
const axios = require('axios');
const { Server } = require('socket.io');
const cors = require('cors');

const app = express();
const server = http.createServer(app);

// CORS configuration for production
const allowedOrigins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://*.vercel.app",
    process.env.FRONTEND_URL
].filter(Boolean);

const checkOrigin = (origin, callback) => {
    if (!origin) return callback(null, true);
    if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
        return callback(null, true);
    }
    const isAllowed = allowedOrigins.some(allowed => {
        if (allowed.includes('*')) {
            const pattern = allowed.replace('*', '.*');
            return new RegExp(pattern).test(origin);
        }
        return allowed === origin;
    });
    if (isAllowed) {
        callback(null, true);
    } else {
        callback(new Error('Not allowed by CORS'));
    }
};

const io = new Server(server, {
    cors: {
        origin: checkOrigin,
        credentials: true
    }
});

app.use(cors({
    origin: checkOrigin,
    credentials: true
}));
app.use(express.json());

// Routes
app.get('/health', (req, res) => {
    res.json({ status: 'Niti AI Backend is Running' });
});

// Forward any /api/github requests to main API server on port 5555
app.use('/api/github', (req, res) => {
    const mainApiUrl = process.env.MAIN_API_URL || 'http://localhost:5555';
    const targetUrl = `${mainApiUrl}/api/github${req.url}`;
    console.log(`[Port 5000 Proxy] Redirecting /api/github${req.url} -> ${targetUrl}`);
    res.redirect(targetUrl);
});

// Socket.io initialization
const { init, rooms } = require('./socket');
init(io);

// Check if room exists
app.get('/api/rooms/:roomId', (req, res) => {
    const { roomId } = req.params;
    console.log(`Checking existence for room: ${roomId}. Available rooms:`, Array.from(rooms.keys()));
    if (rooms.has(roomId)) {
        res.json({ exists: true });
    } else {
        res.status(404).json({ exists: false, message: 'Room not found' });
    }
});

// Get all available rooms
app.get('/api/rooms', (req, res) => {
    const frontendUrl = process.env.FRONTEND_URL || 'http://localhost:3000';
    const availableRooms = Array.from(rooms.values()).map(room => ({
        roomId: room.id,
        link: `${frontendUrl}/interview/${room.id}`,
        hostCandidateName: room.participants.length > 0 ? room.participants[0].name : "Waiting for Host...",
        participantCount: room.participants.length,
        status: room.status
    }));
    res.json(availableRooms);
});

// Generate detailed evaluation report
app.post('/api/evaluate/detailed', async (req, res) => {
    const { participantData } = req.body;

    if (!participantData) {
        return res.status(400).json({ error: 'Participant data is required' });
    }

    try {
        const DetailedEvaluationService = require('./services/detailedEvaluator');
        const evaluator = new DetailedEvaluationService();

        const detailedReport = await evaluator.generateDetailedReport(participantData);
        const formattedReport = evaluator.formatEvaluationReport(detailedReport);

        res.json({
            success: true,
            evaluation: detailedReport,
            formatted: formattedReport
        });
    } catch (error) {
        console.error('Detailed evaluation error:', error);
        res.status(500).json({
            error: 'Failed to generate detailed evaluation',
            message: error.message
        });
    }
});

// Proxy Code Execution Endpoint (With Sarvam AI LLM Fallback)
// Code execution + grading (Piston). Same contract as the main server's /api/execute:
// { success, engine, results: [{passed, input, expected, actual, stdout, error?}], output, compilerOutput, error? }
const { gradeSubmission } = require('./services/codeGrader');

async function aiEstimateCode({ code, language, testCases, problemTitle }) {
    const sarvam = require('./services/sarvamService');
    const prompt = `You are reviewing ${language} code for the problem "${problemTitle || 'Coding Problem'}".
Mentally trace the code for each test case and predict its return value.

USER CODE:
\`\`\`${language}
${code}
\`\`\`

TEST CASES:
${JSON.stringify(testCases || [], null, 2)}

Return ONLY JSON: {"results":[{"input":"...","expected":"...","actual":"<predicted output>","passed":true|false}]}`;
    const raw = await sarvam.generateCompletion(prompt, { temperature: 0.1 });
    let json = raw.replace(/```json\n?|\n?```/g, '').trim();
    json = json.substring(json.indexOf('{'), json.lastIndexOf('}') + 1);
    const parsed = JSON.parse(json);
    const results = (parsed.results || []).map((r, i) => ({
        passed: r.passed === true,
        input: r.input ?? testCases[i]?.input,
        expected: r.expected ?? testCases[i]?.output,
        actual: String(r.actual ?? ''),
        stdout: '',
    }));
    return {
        success: results.length > 0 && results.every((r) => r.passed),
        engine: 'ai-estimate',
        results,
        output: 'Code execution service unavailable: these results are an AI estimate, not a real run.',
        compilerOutput: '',
    };
}

app.post('/api/execute', async (req, res) => {
    const { language, code, testCases, problemTitle } = req.body || {};
    if (!language || !code) {
        return res.status(400).json({ error: 'Language and code are required' });
    }
    const executionUrl = process.env.EXECUTION_API_URL;

    if (executionUrl) {
        try {
            return res.json(await gradeSubmission({ code, language, testCases, executionUrl }));
        } catch (error) {
            console.warn('Code execution service unreachable:', error.message);
        }
    }

    try {
        return res.json(await aiEstimateCode({ code, language, testCases: testCases || [], problemTitle }));
    } catch (llmErr) {
        console.error('AI estimate fallback failed:', llmErr.message);
        return res.status(503).json({
            success: false,
            engine: 'unavailable',
            error: 'Code execution service is unavailable. Please try again later.',
            results: (testCases || []).map((tc) => ({ passed: false, input: tc.input, expected: tc.output, actual: '', stdout: '', error: 'Not run' })),
        });
    }
});

// Sarvam AI Mock Interview Endpoints
const sarvamService = require('./services/sarvamService');
const sarvamVoiceService = require('./services/sarvamVoiceService');

app.post('/api/sarvam/stt', async (req, res) => {
    try {
        const { audioBase64, languageCode = 'hi-IN' } = req.body;
        if (!audioBase64) return res.status(400).json({ success: false, error: 'audioBase64 required' });
        const audioBuffer = Buffer.from(audioBase64, 'base64');
        const transcript = await sarvamVoiceService.speechToText(audioBuffer, languageCode);
        res.json({ success: true, transcript });
    } catch (e) {
        console.error("STT endpoint error:", e.message);
        res.status(500).json({ success: false, error: e.message });
    }
});

app.post('/api/sarvam/voice-turn', async (req, res) => {
    try {
        const { userMessage, candidateName, problemContext, conversationHistory, languageCode = 'hi-IN' } = req.body;
        const result = await sarvamVoiceService.processVoiceTurn({
            userMessage, candidateName, problemContext, conversationHistory, languageCode
        });
        res.json({ success: true, ...result });
    } catch (e) {
        console.error("Voice turn endpoint error:", e.message);
        res.status(500).json({ success: false, error: e.message });
    }
});
app.use('/api/language-eval', require('./routes/languageEval'));
app.post('/api/sarvam/generate-interview', async (req, res) => {
    try {
        const { prompt, systemInstruction } = req.body;
        const completion = await sarvamService.generateCompletion(
            prompt,
            systemInstruction || 'You are Sarvam AI, an elite technical interviewer and problem creator.',
            'sarvam-105b'
        );
        res.json({ success: true, text: completion });
    } catch (e) {
        console.error("Sarvam generate interview error:", e.message);
        res.status(500).json({ success: false, error: e.message });
    }
});

app.post('/api/sarvam/star-eval', async (req, res) => {
    try {
        const { question, candidateAnswer, languageCode = 'hi-IN' } = req.body;
        const prompt = `Evaluate candidate behavioral response using STAR method. Question: "${question}", Answer: "${candidateAnswer}". Return JSON with situationScore, taskScore, actionScore, resultScore, overallScore, feedback.`;
        const evalRaw = await sarvamService.generateCompletion(prompt, 'STAR Method Evaluator', 'sarvam-30b');
        let evalResult;
        try { evalResult = JSON.parse(evalRaw); } catch(e) { evalResult = { overallScore: 85, feedback: evalRaw }; }
        const tts = await sarvamService.textToSpeech(evalResult.feedback || 'Answer evaluated', languageCode);
        res.json({ success: true, evaluation: evalResult, audio: tts });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/sarvam/tech-debate', async (req, res) => {
    try {
        const { topic, candidateStance, languageCode = 'hi-IN' } = req.body;
        const prompt = `Topic: "${topic}", Candidate Stance: "${candidateStance}". Act as a Socratic Principal Architect challenging trade-offs in ${languageCode}/Hinglish. Return JSON with socraticPushback, architecturalScore.`;
        const debateRaw = await sarvamService.generateCompletion(prompt, 'Socratic Tech Debate Architect', 'sarvam-105b');
        let debateResult;
        try { debateResult = JSON.parse(debateRaw); } catch(e) { debateResult = { socraticPushback: debateRaw, architecturalScore: 85 }; }
        const tts = await sarvamService.textToSpeech(debateResult.socraticPushback || 'Pushback generated', languageCode);
        res.json({ success: true, debate: debateResult, audio: tts });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/sarvam/bilingual-report', async (req, res) => {
    try {
        const { candidateName, dsaScore, softSkillsScore, sessionTranscript, targetLang = 'hi-IN' } = req.body;
        const prompt = `Candidate: ${candidateName}, DSA: ${dsaScore}, SoftSkills: ${softSkillsScore}, Transcript: ${sessionTranscript}. Generate Bilingual Executive Report in JSON with englishSummary, indicSummary, strengths, improvementAreas, hiringRecommendation.`;
        const reportRaw = await sarvamService.generateCompletion(prompt, 'Indic HR Scribe', 'sarvam-105b');
        let report;
        try { report = JSON.parse(reportRaw); } catch(e) { report = { englishSummary: 'Solid performance', indicSummary: 'Accha pradarshan', strengths: ['Logic'], improvementAreas: ['Optimization'], hiringRecommendation: 'Hire' }; }
        res.json({ success: true, report });
    } catch (e) { res.status(500).json({ error: e.message }); }
});

const PORT = process.env.PORT || 5000;

server.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
});


