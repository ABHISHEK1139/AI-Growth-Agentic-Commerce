"use client";

import React, { useState, useEffect } from "react";
import {
  X,
  Zap,
  Cpu,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Sliders,
  ExternalLink,
  Shield,
  Eye,
  EyeOff,
  RotateCcw,
} from "lucide-react";
import {
  type AIProviderId,
  type CustomModelConfig,
  PROVIDER_PRESETS,
  getStoredModelConfig,
  saveStoredModelConfig,
  clearStoredModelConfig,
} from "@/catalog/modelConfig";

interface AIModelConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfigSaved?: (config: CustomModelConfig | null) => void;
}

export function AIModelConfigModal({
  isOpen,
  onClose,
  onConfigSaved,
}: AIModelConfigModalProps) {
  const [selectedProvider, setSelectedProvider] = useState<AIProviderId>("grok");
  const [baseUrl, setBaseUrl] = useState("https://api.x.ai/v1");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("grok-2-latest");
  const [showKey, setShowKey] = useState(false);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    message?: string;
    error?: string;
    latencyMs?: number;
  } | null>(null);

  // Load stored config on open
  useEffect(() => {
    if (!isOpen) return;
    const stored = getStoredModelConfig();
    if (stored) {
      setSelectedProvider(stored.providerId);
      setBaseUrl(stored.baseUrl);
      setApiKey(stored.apiKey || "");
      setModelName(stored.modelName);
    } else {
      const preset = PROVIDER_PRESETS.grok;
      setSelectedProvider("grok");
      setBaseUrl(preset.defaultBaseUrl);
      setApiKey("");
      setModelName(preset.defaultModel);
    }
    setTestResult(null);
  }, [isOpen]);

  const handleSelectPreset = (pId: AIProviderId) => {
    const preset = PROVIDER_PRESETS[pId];
    setSelectedProvider(pId);
    setBaseUrl(preset.defaultBaseUrl);
    setModelName(preset.defaultModel);
    setTestResult(null);
  };

  const handleTestConnection = async () => {
    if (!baseUrl.trim()) return;
    setTesting(true);
    setTestResult(null);

    try {
      const res = await fetch("/api/ai/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseUrl: baseUrl.trim(),
          apiKey: apiKey.trim(),
          modelName: modelName.trim() || "default",
          providerId: selectedProvider,
        }),
      });

      const data = await res.json();
      if (res.ok && data.ok) {
        setTestResult({
          ok: true,
          message: data.message || `Connection successful (${data.latencyMs}ms)`,
          latencyMs: data.latencyMs,
        });
      } else {
        setTestResult({
          ok: false,
          error: data.error || `HTTP error ${res.status}`,
        });
      }
    } catch (err: any) {
      setTestResult({
        ok: false,
        error: err?.message || "Failed to reach endpoint",
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = () => {
    const newConfig: CustomModelConfig = {
      providerId: selectedProvider,
      baseUrl: baseUrl.trim(),
      apiKey: apiKey.trim(),
      modelName: modelName.trim() || PROVIDER_PRESETS[selectedProvider].defaultModel,
      displayName: PROVIDER_PRESETS[selectedProvider].name,
    };

    saveStoredModelConfig(newConfig);
    if (onConfigSaved) onConfigSaved(newConfig);
    onClose();
  };

  const handleReset = () => {
    clearStoredModelConfig();
    const preset = PROVIDER_PRESETS.grok;
    setSelectedProvider("grok");
    setBaseUrl(preset.defaultBaseUrl);
    setApiKey("");
    setModelName(preset.defaultModel);
    setTestResult(null);
    if (onConfigSaved) onConfigSaved(null);
    onClose();
  };

  if (!isOpen) return null;

  const currentPreset = PROVIDER_PRESETS[selectedProvider];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-fade-in">
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="p-4 bg-slate-900 text-white flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="p-2 bg-amber-400 text-slate-950 rounded-xl text-xs font-bold">
              <Sliders className="h-4 w-4" />
            </span>
            <div>
              <h3 className="font-bold text-base">Configure AI Model</h3>
              <p className="text-xs text-slate-400">
                Plug in your own xAI Grok key, local Ollama, LM Studio, or custom API
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Modal Content */}
        <div className="p-5 overflow-y-auto space-y-4 text-xs">
          {/* Preset Selector Grid */}
          <div>
            <label className="text-xs font-bold text-slate-700 block mb-2">
              Select Provider / Architecture:
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {(Object.keys(PROVIDER_PRESETS) as AIProviderId[]).map((pId) => {
                const p = PROVIDER_PRESETS[pId];
                const isSelected = selectedProvider === pId;
                return (
                  <button
                    key={pId}
                    type="button"
                    onClick={() => handleSelectPreset(pId)}
                    className={`p-2.5 rounded-xl border text-left transition-all flex flex-col justify-between ${
                      isSelected
                        ? "border-amber-500 bg-amber-50/60 ring-2 ring-amber-400/30 shadow-xs"
                        : "border-slate-200 hover:border-slate-300 bg-white hover:bg-slate-50 text-slate-800"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-bold text-xs text-slate-900">{p.name}</span>
                      <span className={`text-[9px] font-bold px-1.5 py-0.2 rounded ${
                        p.isLocal ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-600"
                      }`}>
                        {p.badge}
                      </span>
                    </div>
                    <p className="text-[10px] text-slate-500 line-clamp-2">{p.description}</p>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Endpoint Base URL */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="font-bold text-slate-700">Endpoint Base URL:</label>
              {currentPreset.isLocal && (
                <span className="text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-200">
                  Localhost / Loopback allowed
                </span>
              )}
            </div>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="e.g. http://localhost:11434/v1 or https://api.x.ai/v1"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-slate-900 font-mono text-xs focus:ring-2 focus:ring-amber-500 focus:outline-none"
            />
            <p className="text-[10px] text-slate-500">
              Standard OpenAI-compatible completions endpoint format. `/chat/completions` will be appended automatically.
            </p>
          </div>

          {/* Model Name */}
          <div className="space-y-1">
            <label className="font-bold text-slate-700">Model Name / Identifier:</label>
            <input
              type="text"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder="e.g. grok-2-latest, llama3.2, mistral, deepseek-chat"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-slate-900 font-mono text-xs focus:ring-2 focus:ring-amber-500 focus:outline-none"
            />
          </div>

          {/* API Key Input */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="font-bold text-slate-700">Provider API Key:</label>
              {!currentPreset.requiresKey && (
                <span className="text-[10px] font-semibold text-slate-500">
                  Optional (Not needed for local models)
                </span>
              )}
            </div>
            <div className="relative">
              <input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={currentPreset.keyPlaceholder}
                className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 pr-10 text-slate-900 font-mono text-xs focus:ring-2 focus:ring-amber-500 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-600"
              >
                {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            <p className="text-[10px] text-slate-500">
              Keys are kept in your local browser storage and sent directly to your configured endpoint.
            </p>
          </div>

          {/* Test Connection Results */}
          {testResult && (
            <div
              className={`p-3 rounded-xl border flex items-start gap-2.5 ${
                testResult.ok
                  ? "bg-emerald-50 border-emerald-200 text-emerald-900"
                  : "bg-rose-50 border-rose-200 text-rose-900"
              }`}
            >
              {testResult.ok ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-600 flex-shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="h-4 w-4 text-rose-600 flex-shrink-0 mt-0.5" />
              )}
              <div className="text-xs">
                <p className="font-bold">
                  {testResult.ok ? "Connection Successful" : "Connection Failed"}
                </p>
                <p className="text-[11px] mt-0.5 opacity-90">
                  {testResult.ok ? testResult.message : testResult.error}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="p-4 bg-slate-50 border-t border-slate-200 flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            onClick={handleReset}
            className="flex items-center gap-1 text-xs text-slate-600 hover:text-rose-600 font-semibold transition"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Reset to Default</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleTestConnection}
              disabled={testing || !baseUrl.trim()}
              className="px-3 py-2 text-xs font-bold rounded-xl border border-slate-300 bg-white hover:bg-slate-100 text-slate-700 transition flex items-center gap-1.5 disabled:opacity-50"
            >
              {testing ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-500" />
                  <span>Testing...</span>
                </>
              ) : (
                <>
                  <Zap className="h-3.5 w-3.5 text-amber-500" />
                  <span>Test Connection</span>
                </>
              )}
            </button>

            <button
              type="button"
              onClick={handleSave}
              className="px-4 py-2 text-xs font-bold rounded-xl bg-slate-950 text-white hover:bg-slate-800 transition shadow-sm"
            >
              Save & Activate
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
