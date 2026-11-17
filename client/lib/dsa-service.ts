import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export interface DSAQuestion {
    title: string;
    description: string;
    difficulty: "Easy" | "Medium" | "Hard";
    example: {
        input: string;
        output: string;
    };
    testCases: Array<{
        input: string;
        output: string;
    }>;
    boilerplates: {
        javascript: string;
        python: string;
        cpp: string;
    };
}

export const FALLBACK_QUESTIONS: DSAQuestion[] = [
    {
        title: "Low-Latency Rate Limiter (Token Bucket)",
        description: "Design and implement a token bucket rate limiter class that allows up to `capacity` requests every `refillRate` seconds. Given an array of request timestamps in milliseconds, return an array of booleans indicating whether each request was accepted (`true`) or rate-limited (`false`).",
        difficulty: "Medium",
        example: {
            input: "capacity = 3, refillRate = 1000, requests = [0, 100, 200, 300, 1100]",
            output: "[true, true, true, false, true]"
        },
        testCases: [
            { input: "3, 1000, [0, 100, 200, 300, 1100]", output: "[true, true, true, false, true]" },
            { input: "2, 500, [0, 100, 200]", output: "[true, true, false]" }
        ],
        boilerplates: {
            javascript: "// Write your solution below\nfunction solution(capacity, refillRate, requests) {\n  // Your code here\n  return [];\n}",
            python: "# Write your solution below\ndef solution(capacity, refillRate, requests):\n    # Your code here\n    return []",
            cpp: "// Write your solution below\nclass Solution {\npublic:\n    vector<bool> solution(int capacity, int refillRate, vector<int> requests) {\n        // Your code here\n        return {};\n    }\n};"
        }
    },
    {
        title: "LRU Cache Memory Management",
        description: "Design a data structure that follows the constraints of a Least Recently Used (LRU) cache with fixed `capacity`. Implement `get(key)` and `put(key, value)` with O(1) average time complexity.",
        difficulty: "Medium",
        example: {
            input: "capacity = 2, actions = ['put(1,1)', 'put(2,2)', 'get(1)']",
            output: "[null, null, 1]"
        },
        testCases: [
            { input: "2, ['put(1,1)', 'put(2,2)', 'get(1)']", output: "[null, null, 1]" }
        ],
        boilerplates: {
            javascript: "// Write your solution below\nfunction solution(capacity, actions) {\n  // Your code here\n  return [];\n}",
            python: "# Write your solution below\ndef solution(capacity, actions):\n    # Your code here\n    return []",
            cpp: "// Write your solution below\nclass Solution {\npublic:\n    vector<int> solution(int capacity, vector<string> actions) {\n        // Your code here\n        return {};\n    }\n};"
        }
    }
];

export async function generateCodingQuestions(
    domain: string = "DSA",
    difficulty: string = "Medium",
    topic: string = "General"
): Promise<DSAQuestion[]> {
    try {
        const response = await fetch("http://localhost:5555/api/coding/generate", {
            method: "POST",
            credentials: "include",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ domain, difficulty, topic })
        });

        if (response.ok) {
            const data = await response.json();
            if (Array.isArray(data.questions) && data.questions.length > 0) {
                return data.questions;
            }
        }
    } catch (error) {
        console.warn("Sarvam AI question generation API error, using fallback dataset:", error);
    }
    return FALLBACK_QUESTIONS;
}



export interface TestResult {
    passed: boolean;
    input: string;
    expected: string;
    actual: string;
    stdout?: string;
    error?: string;
}

export interface ExecutionResult {
    success: boolean;
    results: TestResult[];
    compilerOutput: string;
    error?: string;
    engine?: "piston" | "ai-estimate" | "unavailable";
}

// Runs the candidate's code on the server (/api/execute), which executes it on Piston against
// every test case and compares actual vs expected output.
export async function executeCode(code: string, language: string, testCases: any[]): Promise<ExecutionResult> {
    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5555";
    try {
        const response = await fetch(`${API_URL}/api/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code, language, testCases }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok && !data.results) {
            throw new Error(data.error || `Execution failed (${response.status})`);
        }
        return {
            success: data.success === true,
            results: Array.isArray(data.results) ? data.results : [],
            compilerOutput: [data.engine === "ai-estimate" ? data.output : "", data.compilerOutput || ""].filter(Boolean).join("\n"),
            error: data.error,
            engine: data.engine,
        };
    } catch (error: any) {
        console.error("Evaluation Error:", error);
        return {
            success: false,
            results: [],
            compilerOutput: "",
            error: `Error executing code: ${error.message}`,
        };
    }
}
