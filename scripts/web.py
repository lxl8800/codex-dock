from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .service import CodexService


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>codex-dock</title>
  <link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><defs><linearGradient id="g" x1="17" y1="8" x2="55" y2="64" gradientUnits="userSpaceOnUse"><stop stop-color="%239e94ff"/><stop offset=".5" stop-color="%236fa4ff"/><stop offset="1" stop-color="%232d22e8"/></linearGradient><filter id="s" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="5" stdDeviation="4" flood-color="%232719c8" flood-opacity=".38"/></filter></defs><g filter="url(%23s)" fill="url(%23g)"><circle cx="36" cy="15" r="16"/><circle cx="54" cy="26" r="16"/><circle cx="54" cy="46" r="16"/><circle cx="36" cy="57" r="16"/><circle cx="18" cy="46" r="16"/><circle cx="18" cy="26" r="16"/><path d="M36 15 54 26v20L36 57 18 46V26Z"/></g><path d="M26 24l10 12-10 12" fill="none" stroke="%23eef4ff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/><path d="M44 48h14" fill="none" stroke="%23eef4ff" stroke-width="6" stroke-linecap="round" opacity=".9"/><path d="M20 27c4-7 10-11 18-11 6 0 11 2 15 6" fill="none" stroke="%23ffffff" stroke-width="2" stroke-linecap="round" opacity=".3"/></svg>'>
  <style>
    :root {
      --bg-1: #13111e;
      --bg-2: #22182b;
      --panel: rgba(255,255,255,.08);
      --panel-strong: rgba(255,255,255,.11);
      --border: rgba(255,255,255,.12);
      --text: #f6f2ff;
      --muted: #b9b1cb;
      --muted-strong: #d5cfee;
      --green: #35d39a;
      --green-soft: rgba(53,211,154,.15);
      --blue: #6f84ff;
      --blue-soft: rgba(111,132,255,.18);
      --red: #f39b8c;
      --red-soft: rgba(243,155,140,.16);
      --shadow: 0 20px 60px rgba(0,0,0,.28);
      --radius-xl: 28px;
      --radius-lg: 24px;
      --radius-md: 18px;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      color: var(--text);
      font-family: "SF Pro Display", "Aptos", "Segoe UI", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at 12% 16%, rgba(113,144,255,.24), transparent 24%),
        radial-gradient(circle at 88% 18%, rgba(53,211,154,.10), transparent 18%),
        radial-gradient(circle at 82% 82%, rgba(180,98,255,.14), transparent 24%),
        linear-gradient(135deg, var(--bg-1), var(--bg-2) 44%, #17131d 100%);
      overflow: hidden;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(255,255,255,.03), rgba(255,255,255,0)),
        repeating-linear-gradient(
          135deg,
          rgba(255,255,255,.018) 0 1px,
          transparent 1px 18px
        );
      opacity: .9;
    }
    .app {
      position: relative;
      height: 100%;
      display: grid;
      grid-template-rows: auto auto 1fr;
      gap: 0;
      padding: 10px 12px 12px;
      border-radius: 24px;
      overflow: hidden;
    }
    .glass {
      background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.05));
      backdrop-filter: blur(24px) saturate(130%);
      -webkit-backdrop-filter: blur(24px) saturate(130%);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    .hero {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 18px;
      padding: 16px 18px;
      border-radius: 0;
      border: 0;
      box-shadow: none;
      background: transparent;
    }
    .eyebrow {
      color: var(--muted);
      font-size: 10px;
      letter-spacing: .18em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .hero h1 {
      margin: 0;
      font-size: 28px;
      letter-spacing: -.05em;
      font-weight: 800;
    }
    .brand-title {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .app-mark {
      width: 42px;
      height: 42px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      filter: drop-shadow(0 10px 18px rgba(72,62,224,.32));
      flex: none;
    }
    .app-mark svg {
      width: 100%;
      height: 100%;
      display: block;
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .btn {
      appearance: none;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.08);
      color: var(--text);
      border-radius: 999px;
      padding: 11px 16px;
      font: 800 13px/1 "SF Pro Display", "Aptos", "Segoe UI", sans-serif;
      cursor: pointer;
      transition: transform .18s ease, background .18s ease, border-color .18s ease;
    }
    .btn:hover { transform: translateY(-1px); }
    .btn.soft { background: rgba(255,255,255,.06); }
    .btn.blue { background: var(--blue-soft); border-color: rgba(111,132,255,.30); }
    .btn.green { background: var(--green-soft); border-color: rgba(53,211,154,.26); }
    .btn.red { background: var(--red-soft); border-color: rgba(243,155,140,.24); }
    .tabs {
      display: grid;
      grid-template-columns: auto 1fr;
      align-items: stretch;
      gap: 12px;
      padding: 0 18px 12px;
      border-radius: 0;
      border: 0;
      box-shadow: none;
      background: transparent;
    }
    .tab-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .tip-wrap {
      position: relative;
      display: inline-flex;
    }
    .tip-wrap::after {
      content: attr(data-tip);
      position: absolute;
      left: 50%;
      top: calc(100% + 10px);
      transform: translateX(-50%) translateY(4px);
      min-width: 220px;
      max-width: 280px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(17, 14, 29, .96);
      border: 1px solid rgba(255,255,255,.12);
      color: var(--muted-strong);
      font: 600 12px/1.5 "SF Pro Display", "Aptos", "Segoe UI", sans-serif;
      box-shadow: 0 14px 30px rgba(0,0,0,.32);
      pointer-events: none;
      opacity: 0;
      white-space: normal;
      z-index: 20;
      transition: opacity .18s ease, transform .18s ease;
    }
    .tip-wrap:hover::after,
    .tip-wrap:focus-within::after {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
    .tab {
      flex: none;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.05);
      color: var(--muted);
      border-radius: 999px;
      padding: 10px 15px;
      font: 800 13px/1 "SF Pro Display", "Aptos", "Segoe UI", sans-serif;
      cursor: pointer;
    }
    .tab.active {
      color: var(--text);
      background: rgba(111,132,255,.20);
      border-color: rgba(111,132,255,.36);
    }
    .views {
      min-height: 0;
      position: relative;
      padding: 0 14px;
    }
    .view {
      display: none;
      min-height: 0;
      height: 100%;
    }
    .view.active { display: grid; }
    .dashboard-view {
      grid-template-rows: auto 1fr;
      gap: 10px;
      min-height: 0;
    }
    .detached-alert {
      display: none;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 16px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(111,132,255,.16), rgba(111,132,255,.08));
      border: 1px solid rgba(111,132,255,.24);
    }
    .detached-alert.show {
      display: flex;
    }
    .detached-copy strong {
      display: block;
      font-size: 14px;
      letter-spacing: -.02em;
    }
    .detached-copy span {
      display: block;
      margin-top: 4px;
      color: var(--muted-strong);
      font-size: 12px;
      line-height: 1.55;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 18px;
      overflow: hidden;
      border-radius: 0;
      border: 0;
      align-items: center;
    }
    .summary-card {
      min-height: 0;
      padding: 0;
      border-radius: 0;
      border: 0;
      box-shadow: none;
      background: transparent;
    }
    .summary-card:last-child {
      border-right: 0;
    }
    .summary-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: .12em;
    }
    .summary-value {
      margin-top: 8px;
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -.04em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .board {
      min-height: 0;
      overflow: auto;
      padding: 10px;
      border-radius: 18px;
      box-shadow: none;
      background: rgba(255,255,255,.035);
      scrollbar-width: thin;
      scrollbar-color: rgba(255,255,255,.24) rgba(255,255,255,.04);
    }
    .board::-webkit-scrollbar { width: 10px; }
    .board::-webkit-scrollbar-track {
      border-radius: 999px;
      background: rgba(255,255,255,.04);
      border: 1px solid rgba(255,255,255,.04);
    }
    .board::-webkit-scrollbar-thumb {
      border-radius: 999px;
      background: linear-gradient(180deg, rgba(255,255,255,.24), rgba(255,255,255,.10));
      border: 2px solid transparent;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      padding: 2px 0 8px;
    }
    .card {
      border-radius: 18px;
      padding: 12px;
      position: relative;
      overflow: hidden;
      min-height: 210px;
    }
    .card::after {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(255,255,255,.02), transparent 46%);
      pointer-events: none;
    }
    .card.current {
      border-color: rgba(53,211,154,.36);
      box-shadow: 0 18px 60px rgba(53,211,154,.10), var(--shadow);
    }
    .card-head {
      display: grid;
      grid-template-columns: auto auto minmax(0, 1fr);
      align-items: center;
      column-gap: 9px;
      row-gap: 6px;
    }
    .status-dot {
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: rgba(0,0,0,.45);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.06);
      flex: none;
    }
    .status-dot.live {
      background: var(--green);
      box-shadow: 0 0 14px rgba(53,211,154,.55);
    }
    .logo {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: transparent;
      box-shadow: 0 10px 20px rgba(72,62,224,.20);
      flex: none;
    }
    .logo svg {
      width: 36px;
      height: 36px;
      display: block;
    }
    .meta {
      min-width: 0;
      flex: 1;
      display: grid;
      grid-template-rows: auto auto;
      gap: 0;
    }
    .pills {
      display: flex;
      gap: 6px;
      margin-bottom: 6px;
      flex-wrap: nowrap;
      overflow: hidden;
      min-width: 0;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      height: 22px;
      padding: 0 9px;
      border-radius: 999px;
      background: rgba(255,255,255,.08);
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      flex: none;
      white-space: nowrap;
    }
    .pill.codex {
      background: rgba(111,132,255,.26);
      color: #dce2ff;
    }
    .pill.member {
      background: rgba(53,211,154,.16);
      color: #baf8dd;
    }
    .pill.warn {
      background: rgba(243,155,140,.16);
      color: #ffd4cb;
    }
    .email {
      font-size: 14px;
      font-weight: 800;
      letter-spacing: -.03em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .meta-line {
      margin-top: 10px;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 11px;
    }
    .divider {
      height: 1px;
      background: rgba(255,255,255,.08);
      margin: 10px 0;
    }
    .usage {
      margin-bottom: 10px;
    }
    .usage-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
      margin-bottom: 7px;
      font-size: 12px;
    }
    .usage-title { font-weight: 800; }
    .usage-side {
      display: flex;
      gap: 8px;
      align-items: baseline;
      color: var(--muted);
      font-size: 11px;
    }
    .usage-side strong {
      color: var(--text);
      font-size: 13px;
    }
    .track {
      height: 8px;
      border-radius: 999px;
      background: rgba(255,255,255,.07);
      overflow: hidden;
      box-shadow: inset 0 1px 2px rgba(0,0,0,.24);
    }
    .fill {
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #2bd09d, #34ddb0);
      box-shadow: 0 0 20px rgba(53,211,154,.30);
    }
    .tools {
      display: flex;
      gap: 5px;
      flex-wrap: nowrap;
      min-width: 0;
    }
    .mini-btn {
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(255,255,255,.06);
      color: var(--text);
      border-radius: 12px;
      flex: 1 1 0;
      min-width: 0;
      padding: 8px 6px;
      font-size: 11px;
      font-weight: 800;
      line-height: 1;
      cursor: pointer;
      white-space: nowrap;
    }
    .mini-btn.green { background: rgba(53,211,154,.14); color: #93f1cb; }
    .mini-btn.blue { background: rgba(111,132,255,.16); color: #dce2ff; }
    .mini-btn.red { background: rgba(243,155,140,.14); color: #ffc7bd; }
    .guide-view {
      min-height: 0;
      overflow: auto;
      padding-right: 4px;
      align-content: start;
    }
    .guide-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      align-content: start;
    }
    .guide-card {
      border-radius: 18px;
      padding: 16px;
    }
    .guide-card h3 {
      margin: 0 0 10px;
      font-size: 18px;
      letter-spacing: -.03em;
    }
    .guide-card p, .guide-card li {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.7;
    }
    .guide-card ol, .guide-card ul {
      margin: 0;
      padding-left: 20px;
    }
    .guide-note {
      margin-top: 12px;
      padding: 12px 13px;
      border-radius: 14px;
      background: rgba(111,132,255,.12);
      color: var(--text);
      font-size: 13px;
      line-height: 1.6;
    }
    .guide-note.warn {
      background: rgba(240,201,109,.12);
      border: 1px solid rgba(240,201,109,.16);
    }
    .kbd {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 28px;
      padding: 0 8px;
      margin: 0 2px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,.16);
      background: rgba(255,255,255,.06);
      color: var(--text);
      font: 700 12px/1 "SF Mono", "Consolas", monospace;
      height: 24px;
    }
    .footer {
      margin: 10px 14px 14px;
      padding: 9px 12px;
      border-radius: 16px;
      color: var(--muted);
      font-size: 12px;
    }
    .empty {
      padding: 24px 14px;
      text-align: center;
      color: var(--muted);
      border-radius: 20px;
    }
    .overlay {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: rgba(8,8,12,.42);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      z-index: 50;
    }
    .overlay.show { display: flex; }
    .dialog {
      width: min(560px, 100%);
      border-radius: 24px;
      padding: 18px;
    }
    .dialog-badge {
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 10px;
      margin-bottom: 10px;
      border-radius: 999px;
      background: rgba(111,132,255,.18);
      color: #dde4ff;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .dialog h3 {
      margin: 0;
      font-size: 19px;
      letter-spacing: -.04em;
    }
    .dialog p {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .dialog-hero {
      margin-top: 10px;
      padding: 12px 13px;
      border-radius: 16px;
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.09);
    }
    .dialog-hero strong {
      display: block;
      margin-bottom: 4px;
      color: var(--text);
      font-size: 12px;
    }
    .dialog-hero span {
      color: var(--muted-strong);
      font-size: 11px;
      line-height: 1.55;
    }
    .dialog-tip {
      margin-top: 10px;
      padding: 12px 13px;
      border-radius: 16px;
      background: rgba(243,155,140,.10);
      border: 1px solid rgba(243,155,140,.18);
    }
    .dialog-tip strong {
      display: block;
      margin-bottom: 8px;
      color: var(--text);
      font-size: 12px;
    }
    .dialog-tip ul {
      margin: 0;
      padding-left: 18px;
      color: var(--muted-strong);
      font-size: 11px;
      line-height: 1.55;
    }
    .dialog-tip li + li {
      margin-top: 4px;
    }
    .dialog input {
      width: 100%;
      margin-top: 14px;
      border: 1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.08);
      color: var(--text);
      border-radius: 14px;
      padding: 12px 13px;
      font: 700 13px/1 "SF Pro Display", "Aptos", "Segoe UI", sans-serif;
      outline: none;
    }
    .dialog-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
      flex-wrap: wrap;
    }
    .toast {
      position: fixed;
      left: 20px;
      bottom: 20px;
      z-index: 80;
      display: grid;
      grid-template-columns: 12px 1fr;
      align-items: center;
      gap: 12px;
      min-width: 240px;
      max-width: min(480px, calc(100vw - 40px));
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid rgba(255,255,255,.16);
      background: linear-gradient(180deg, rgba(40,38,58,.86), rgba(25,24,36,.72));
      box-shadow: 0 18px 50px rgba(0,0,0,.32);
      backdrop-filter: blur(22px) saturate(135%);
      -webkit-backdrop-filter: blur(22px) saturate(135%);
      color: var(--text);
      font-size: 13px;
      font-weight: 800;
      line-height: 1.45;
      pointer-events: none;
      opacity: 0;
      transform: translateY(12px) scale(.98);
      transition: opacity .2s ease, transform .2s ease;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
    .toast-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--blue);
      box-shadow: 0 0 18px rgba(111,132,255,.55);
    }
    .toast.success .toast-dot {
      background: var(--green);
      box-shadow: 0 0 18px rgba(53,211,154,.55);
    }
    .toast.error .toast-dot {
      background: var(--red);
      box-shadow: 0 0 18px rgba(243,155,140,.55);
    }
    .toast.loading .toast-dot {
      animation: toastPulse 1s ease-in-out infinite;
    }
    @keyframes toastPulse {
      0%, 100% { opacity: .45; transform: scale(.82); }
      50% { opacity: 1; transform: scale(1.12); }
    }
    @media (max-width: 1280px) {
      .tabs { grid-template-columns: 1fr; }
      .summary { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .guide-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 920px) {
      .app { padding: 10px; }
      .hero { flex-direction: column; align-items: stretch; }
      .actions { justify-content: flex-start; }
      .tabs { grid-template-columns: 1fr; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .guide-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app glass">
    <section class="hero">
      <div>
        <div class="eyebrow">账号管理中枢</div>
        <div class="brand-title">
          <span class="app-mark" aria-hidden="true">
            <svg viewBox="0 0 72 72" role="img">
              <defs>
                <linearGradient id="appIconGradient" x1="17" y1="8" x2="55" y2="64" gradientUnits="userSpaceOnUse">
                  <stop stop-color="#9e94ff"/>
                  <stop offset=".5" stop-color="#6fa4ff"/>
                  <stop offset="1" stop-color="#2d22e8"/>
                </linearGradient>
              </defs>
              <g fill="url(#appIconGradient)">
                <circle cx="36" cy="15" r="16"/>
                <circle cx="54" cy="26" r="16"/>
                <circle cx="54" cy="46" r="16"/>
                <circle cx="36" cy="57" r="16"/>
                <circle cx="18" cy="46" r="16"/>
                <circle cx="18" cy="26" r="16"/>
                <path d="M36 15 54 26v20L36 57 18 46V26Z"/>
              </g>
              <path d="M26 24l10 12-10 12" fill="none" stroke="#eef4ff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>
              <path d="M44 48h14" fill="none" stroke="#eef4ff" stroke-width="6" stroke-linecap="round" opacity=".9"/>
              <path d="M20 27c4-7 10-11 18-11 6 0 11 2 15 6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity=".3"/>
            </svg>
          </span>
          <h1>codex-dock</h1>
        </div>
      </div>
      <div class="actions">
        <span class="tip-wrap" data-tip="同步全部已保存账号的额度视图，速度更快，适合日常查看。">
          <button class="btn green" onclick="refreshAllAccounts(false)">刷新额度</button>
        </span>
        <span class="tip-wrap" data-tip="逐个账号请求官网 usage 接口，数据更准，但更慢，也更容易触发限制。">
          <button class="btn blue" onclick="refreshAllAccounts(true)">精准刷新</button>
        </span>
        <span class="tip-wrap" data-tip="把当前已经登录的账号保存到本工具里；如果别名已存在，会用当前登录信息覆盖更新。">
          <button class="btn blue" onclick="addAccount()">添加当前账号</button>
        </span>
        <span class="tip-wrap" data-tip="清理当前登录态并回到默认干净环境，适合重新登录新账号或重新开始一组切换流程。">
          <button class="btn red" onclick="switchDefault()">切换默认环境</button>
        </span>
        <span
          class="tip-wrap"
          data-tip="开启后，系统会按更保守的随机队列节奏，在后台串行刷新已保存账号的登录凭据；关闭后不会自动保活。建议仅在确实需要长期保活时开启。"
        >
          <button id="toggleKeepaliveBtn" class="btn soft" onclick="toggleKeepalive()">保活已关闭</button>
        </span>
        <button class="btn soft" onclick="shutdownPanel()">关闭服务</button>
      </div>
    </section>
    <nav class="tabs">
      <div class="tab-actions">
        <button id="tab-dashboard" class="tab active" onclick="switchTab('dashboard')">账号总览</button>
        <button id="tab-guide" class="tab" onclick="switchTab('guide')">操作说明</button>
        <button id="toggleEmailMaskBtn" class="tab" onclick="toggleEmailMask()">显示完整邮箱</button>
      </div>
      <section id="summary" class="summary"></section>
    </nav>
    <main class="views">
      <section id="dashboardView" class="view dashboard-view active">
        <section id="detachedAlert" class="detached-alert glass">
          <div class="detached-copy">
            <strong>当前使用的账户还没有保存到 codex-dock</strong>
            <span>上方摘要里已经显示了当前账号，但它还没有保存到本工具。建议先保存当前账户，这样后面才能直接切换回来，也能在列表里看到它的额度卡片。</span>
          </div>
          <button class="btn blue" onclick="addAccount()">保存当前账户</button>
        </section>
        <section class="board glass">
          <section id="grid" class="grid"></section>
        </section>
      </section>
      <section id="guideView" class="view guide-view">
        <div class="guide-grid">
          <article class="guide-card glass">
            <h3>📖 使用说明</h3>
            <p>这个面板用于统一查看额度、保存账号、切换账号和回到默认干净环境。建议先保存当前账号，再开始新增其他账号，这样最稳。</p>
            <div class="guide-note">下面把“命令行方式”和“网页方式”分开写清楚，并且都按照当前真实按钮名称来说明，照着点就可以。</div>
          </article>
          <article class="guide-card glass">
            <h3>💡 如何保存新账号（命令行方式）</h3>
            <ol>
              <li>请不要在 Codex 软件内点击“退出登录 (Logout)”。</li>
              <li>先运行本工具，按 <span class="kbd">2</span>，菜单名称是“保存当前登录”，把当前已登录账号保存下来，并起一个别名，例如 <span class="kbd">work</span>。</li>
              <li>如果要清空当前登录状态，请按 <span class="kbd">4</span>，菜单名称是“切换账号 / 默认环境”，然后再选 <span class="kbd">0</span> 默认 / 干净状态。</li>
              <li>重新打开 Codex 软件，此时系统会提示重新登录，请登录你的新账号。</li>
              <li>再次运行本工具，再按 <span class="kbd">2</span>“保存当前登录”，把刚登录的新账号也保存进来，例如命名为 <span class="kbd">gmail</span>。</li>
              <li>之后就可以通过 <span class="kbd">4</span>“切换账号 / 默认环境”在这些已收录账号之间自由切换。</li>
            </ol>
            <div class="guide-note">推荐顺序是：先保存当前账号，再切到默认环境登录新账号，最后把新账号再保存进来。这样最不容易丢失旧登录态。</div>
          </article>
          <article class="guide-card glass">
            <h3>🌐 如何保存新账号（网页方式）</h3>
            <ol>
              <li>请不要在 Codex 软件内点击“退出登录 (Logout)”。</li>
              <li>如果当前账号已经登录完成，直接点击顶部按钮 <span class="kbd">添加当前账号</span>，输入别名后确认保存。</li>
              <li>如果你要登录一个全新账号，先点击顶部按钮 <span class="kbd">切换默认环境</span>，让当前环境回到默认 / 干净状态。</li>
              <li>重新打开 Codex 软件并登录新账号。</li>
              <li>回到网页，再点击 <span class="kbd">添加当前账号</span>，把这个新账号保存进去。</li>
              <li>以后需要查看额度时，可以用顶部的 <span class="kbd">刷新额度</span> 或 <span class="kbd">精准刷新</span>；需要回到空白环境时，就点 <span class="kbd">切换默认环境</span>。</li>
            </ol>
            <div class="guide-note">网页方式更适合日常使用：看卡片、点按钮、直接保存。命令行方式则更适合你习惯用数字菜单的时候。</div>
          </article>
          <article class="guide-card glass">
            <h3>刷新与精准刷新的区别和风险</h3>
            <ul>
              <li>顶部按钮 <span class="kbd">刷新额度</span> 会同步全部账户的额度视图，速度更快，适合日常查看。</li>
              <li>顶部按钮 <span class="kbd">精准刷新</span> 会逐个账号直接调用官网接口，数据更实时，但耗时更长。</li>
              <li>两种刷新都会访问官网相关接口，频繁点击可能触发限制或临时风控，请不要连续多次刷新。</li>
              <li>如果你刚切换账号，建议先等页面稳定后再刷新，避免把临时状态误当成最终结果。</li>
            </ul>
            <div class="guide-note warn">简单理解：普通刷新更快，精准刷新更准。前者适合快速查看，后者适合认真核对最新额度时使用。</div>
          </article>
          <article class="guide-card glass">
            <h3>自动刷新与保活开关</h3>
            <ul>
              <li>每次启动服务时，会用当前系统时间对比每个账号的额度重置时间。</li>
              <li>只有当前系统时间已经大于额度重置时间的账号，才会在后台自动执行一次 <span class="kbd">精准刷新</span>。</li>
              <li>如果自动刷新成功并且恢复了额度，这个账号就不会再继续自动刷新。</li>
              <li>如果刷新失败，或刷新成功但额度仍然是 0，会等待 1 分钟再试一次；第二次仍失败或无额度，会再等待 2 分钟做最后一次尝试。</li>
              <li>第三次仍失败或仍无额度时，系统会放弃这个账号的本轮自动刷新，避免频繁请求官网接口。</li>
              <li>顶部按钮 <span class="kbd">保活已关闭</span> / <span class="kbd">保活已开启</span> 用来控制后台 token 保活队列；默认关闭，只有开启后才会自动保活。</li>
            </ul>
            <div class="guide-note warn">这套重试逻辑只用于启动后的后台自动刷新；你手动点击卡片里的 <span class="kbd">刷新</span> / <span class="kbd">精准</span> 或顶部批量按钮时，不会被自动等待 1 分钟、2 分钟。保活开关也只影响 token 保活，不会替代你手动查看额度。</div>
          </article>
          <article class="guide-card glass">
            <h3>保活策略与风险控制</h3>
            <ul>
              <li>开启保活后，系统会按更保守的队列节奏，在后台串行刷新已保存账号的登录凭据。</li>
              <li>默认策略不是固定 24 小时整，而是以上次成功刷新为基准，延后到 <span class="kbd">24 小时 + 2~12 小时随机偏移</span>。</li>
              <li>首次启动不会立刻把所有老账号都补刷，而是为待处理账号安排 <span class="kbd">20~90 分钟</span> 的随机首轮延迟。</li>
              <li>每轮最多只处理 1 个到期账号；如果失败，会退避约 12 小时再尝试。</li>
              <li>如果服务端提示 refresh token 已失效、已被使用或登录凭据无效，这个账号仍然需要重新登录一次后再保存。</li>
            </ul>
            <div class="guide-note">如果你只是日常切换和查看账号，保活可以保持关闭；只有确实需要长期保留多账号登录态时，再手动开启更合适。</div>
          </article>
          <article class="guide-card glass">
            <h3>软件启动说明</h3>
            <ul>
              <li>Windows 建议优先使用 <span class="kbd">start-codex-dock.bat</span>，也可以直接运行 <span class="kbd">python codex.py</span>。</li>
              <li>Linux / macOS 建议使用 <span class="kbd">./start-codex-dock.sh</span> 或 <span class="kbd">python3 codex.py</span>。</li>
              <li>启动后会自动打开本地页面，如果浏览器没有自动弹出，也可以手动访问终端里打印的本地地址。</li>
              <li>每次启动服务时，程序会先对接近到期更新时间的账户做一次后台刷新，尽量保持页面数据和当前状态一致。</li>
              <li>顶部按钮 <span class="kbd">切换默认环境</span> 会清理当前登录态，适合重新开始一组账号管理流程。</li>
            </ul>
            <div class="guide-note">如果你主要是查看和切换账号，平时直接开面板即可；只有当信息明显过期时，再考虑使用精准刷新。</div>
          </article>
        </div>
      </section>
    </main>
  </div>
  <div id="overlay" class="overlay">
    <div class="dialog glass">
      <div class="dialog-badge">操作确认</div>
      <h3 id="dialogTitle"></h3>
      <p id="dialogText"></p>
      <div id="dialogBody"></div>
      <div id="dialogActions" class="dialog-actions"></div>
    </div>
  </div>
  <div id="toast" class="toast" role="status" aria-live="polite">
    <span class="toast-dot"></span>
    <span id="toastText">就绪</span>
  </div>
  <script>
    const summaryEl = document.getElementById("summary");
    const gridEl = document.getElementById("grid");
    const statusEl = document.getElementById("status");
    const toastEl = document.getElementById("toast");
    const toastTextEl = document.getElementById("toastText");
    const overlay = document.getElementById("overlay");
    const dialogTitle = document.getElementById("dialogTitle");
    const dialogText = document.getElementById("dialogText");
    const dialogBody = document.getElementById("dialogBody");
    const dialogActions = document.getElementById("dialogActions");
    const dashboardView = document.getElementById("dashboardView");
    const guideView = document.getElementById("guideView");
    const tabDashboard = document.getElementById("tab-dashboard");
    const tabGuide = document.getElementById("tab-guide");
    const toggleEmailMaskBtn = document.getElementById("toggleEmailMaskBtn");
    const toggleKeepaliveBtn = document.getElementById("toggleKeepaliveBtn");
    const detachedAlert = document.getElementById("detachedAlert");
    let maskEmailsEnabled = true;
    let keepaliveEnabled = false;
    function appIconSvg(idSeed) {
      const gradientId = `cardIconGradient-${String(idSeed || "account").replace(/[^a-zA-Z0-9_-]/g, "_")}`;
      return `
      <svg viewBox="0 0 72 72" aria-hidden="true">
        <defs>
          <linearGradient id="${gradientId}" x1="17" y1="8" x2="55" y2="64" gradientUnits="userSpaceOnUse">
            <stop stop-color="#9e94ff"/>
            <stop offset=".5" stop-color="#6fa4ff"/>
            <stop offset="1" stop-color="#2d22e8"/>
          </linearGradient>
        </defs>
        <g fill="url(#${gradientId})">
          <circle cx="36" cy="15" r="16"/>
          <circle cx="54" cy="26" r="16"/>
          <circle cx="54" cy="46" r="16"/>
          <circle cx="36" cy="57" r="16"/>
          <circle cx="18" cy="46" r="16"/>
          <circle cx="18" cy="26" r="16"/>
          <path d="M36 15 54 26v20L36 57 18 46V26Z"/>
        </g>
        <path d="M26 24l10 12-10 12" fill="none" stroke="#eef4ff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>
        <path d="M44 48h14" fill="none" stroke="#eef4ff" stroke-width="6" stroke-linecap="round" opacity=".9"/>
        <path d="M20 27c4-7 10-11 18-11 6 0 11 2 15 6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity=".3"/>
      </svg>
    `;
    }

    function sleep(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    let toastTimer = null;

    function toastType(message) {
      if (/失败|错误|failed|error/i.test(message)) return "error";
      if (/正在|中|loading|saving|switching|removing|shutting/i.test(message)) return "loading";
      return "success";
    }

    function showToast(message) {
      if (!toastEl || !toastTextEl) return;
      const type = toastType(message);
      toastTextEl.textContent = message;
      toastEl.className = `toast show ${type}`;
      if (toastTimer) clearTimeout(toastTimer);
      const delay = type === "loading" ? 2400 : 3400;
      toastTimer = setTimeout(() => {
        toastEl.classList.remove("show");
      }, delay);
    }

    function setStatus(message) {
      if (statusEl) {
        statusEl.textContent = message;
      }
      showToast(message);
    }

    function switchTab(name) {
      const dashboard = name === "dashboard";
      dashboardView.classList.toggle("active", dashboard);
      guideView.classList.toggle("active", !dashboard);
      tabDashboard.classList.toggle("active", dashboard);
      tabGuide.classList.toggle("active", !dashboard);
    }

    function closeDialog() {
      overlay.classList.remove("show");
      dialogBody.innerHTML = "";
      dialogActions.innerHTML = "";
    }

    function openDialog({ title, text = "", body = null, actions = [] }) {
      dialogTitle.textContent = title;
      dialogText.textContent = text;
      dialogBody.innerHTML = "";
      dialogActions.innerHTML = "";
      if (body) dialogBody.appendChild(body);
      actions.forEach(action => {
        const button = document.createElement("button");
        button.className = `btn ${action.className || "soft"}`.trim();
        button.textContent = action.label;
        button.onclick = action.onClick;
        dialogActions.appendChild(button);
      });
      overlay.classList.add("show");
    }

    function maskEmail(email) {
      if (!email || !email.includes("@")) return email || "N/A";
      const [local, domain] = email.split("@");
      const keep = local.length <= 3 ? 1 : 3;
      return `${local.slice(0, keep)}**@${domain}`;
    }

    function displayEmail(email) {
      return maskEmailsEnabled ? maskEmail(email) : (email || "N/A");
    }

    function updateEmailMaskButton() {
      if (!toggleEmailMaskBtn) return;
      toggleEmailMaskBtn.textContent = maskEmailsEnabled ? "显示完整邮箱" : "隐藏账户邮箱";
      toggleEmailMaskBtn.classList.toggle("active", !maskEmailsEnabled);
    }

    function updateKeepaliveButton(settings = {}) {
      keepaliveEnabled = Boolean(settings.token_keepalive_enabled);
      if (!toggleKeepaliveBtn) return;
      toggleKeepaliveBtn.textContent = keepaliveEnabled ? "保活已开启" : "保活已关闭";
      toggleKeepaliveBtn.classList.toggle("active", keepaliveEnabled);
    }

    function planSupportsFiveHour(plan) {
      const normalized = String(plan || "").trim().toLowerCase();
      return !["", "n/a", "unknown", "free"].includes(normalized);
    }

    function limitsMap(account) {
      const lookup = {};
      (account.usage_limits || []).forEach(limit => {
        lookup[limit.label] = limit;
      });
      return lookup;
    }

    function primaryLimit(account) {
      const lookup = limitsMap(account);
      const preferred = account.is_member || planSupportsFiveHour(account.plan) ? "5h" : "Weekly";
      return lookup[preferred] || Object.values(lookup)[0] || null;
    }

    function summaryCard(label, value) {
      return `
        <article class="summary-card">
          <div class="summary-label">${label}</div>
          <div class="summary-value">${value || "N/A"}</div>
        </article>
      `;
    }

    function usageBlock(limit, plan) {
      if (!limit) {
        const fallbackTitle = planSupportsFiveHour(plan) ? "5小时限额" : "周限额";
        return `
          <div class="usage">
            <div class="usage-head">
              <div class="usage-title">${fallbackTitle}</div>
              <div class="usage-side"><span>N/A</span><strong>0%</strong></div>
            </div>
            <div class="track"><div class="fill" style="width:0%"></div></div>
          </div>
        `;
      }
      const title = limit.label === "5h" ? "5小时限额" : limit.label === "Weekly" ? "周限额" : limit.label;
      const percent = Math.max(0, Math.min(100, Number(limit.left_percent || 0)));
      return `
        <div class="usage">
          <div class="usage-head">
            <div class="usage-title">${title}</div>
            <div class="usage-side"><span>${limit.reset_at || "N/A"}</span><strong>${percent.toFixed(0)}%</strong></div>
          </div>
          <div class="track"><div class="fill" style="width:${percent}%"></div></div>
        </div>
      `;
    }

    function renderSummary(data) {
      const current = data.accounts.find(account => account.is_current) || {};
      const limit = primaryLimit({ ...current, plan: data.current.plan || current.plan });
      const quotaLabel = planSupportsFiveHour(data.current.plan || current.plan) ? "5 小时额度" : "周限额";
      const quotaCount = data.accounts.filter(account => {
        const accountLimit = primaryLimit(account);
        return accountLimit && Number(accountLimit.left_percent || 0) > 0;
      }).length;
      const noQuotaCount = Math.max(0, data.accounts.length - quotaCount);
      summaryEl.innerHTML = [
        summaryCard("当前账号", data.current.alias),
        summaryCard("邮箱", displayEmail(data.current.email)),
        summaryCard("套餐", String(data.current.plan || "N/A").toUpperCase()),
        summaryCard(quotaLabel, limit ? `${Number(limit.left_percent || 0).toFixed(0)}%` : "N/A"),
        summaryCard("重置时间", limit ? limit.reset_at : "N/A"),
        summaryCard("账号数", String(data.accounts.length)),
        summaryCard("有额度", String(quotaCount)),
        summaryCard("无额度", String(noQuotaCount))
      ].join("");
    }

    function updateDetachedAlert(data) {
      if (!detachedAlert) return;
      const hasSavedCurrent = data.accounts.some(account => account.is_current);
      const hasActiveLogin = Boolean(data.current && data.current.email && data.current.email !== "N/A");
      detachedAlert.classList.toggle("show", hasActiveLogin && !hasSavedCurrent);
    }

    function renderCards(data) {
      if (!data.accounts.length) {
        gridEl.innerHTML = '<section class="empty glass">暂无账号，先添加当前已经登录的账号。</section>';
        return;
      }
      gridEl.innerHTML = data.accounts.map(account => {
        const limit = primaryLimit(account);
        const aliasArg = encodeAlias(account.alias);
        return `
          <article class="card glass ${account.is_current ? "current" : ""}">
            <div class="card-head">
              <div class="status-dot ${account.is_current ? "live" : ""}"></div>
              <div class="logo">${appIconSvg(account.alias)}</div>
              <div class="meta">
                <div class="pills">
                  <span class="pill ${account.refresh_due ? "warn" : "codex"}">${account.refresh_due ? "临期" : "Codex"}</span>
                  <span class="pill ${limit && Number(limit.left_percent || 0) > 0 ? "member" : ""}">${limit && Number(limit.left_percent || 0) > 0 ? "有额度" : "无额度"}</span>
                  <span class="pill ${account.is_member ? "member" : ""}">${account.is_member ? "会员" : "普通"}</span>
                </div>
                <div class="email">${displayEmail(account.email)}</div>
              </div>
            </div>
            <div class="meta-line">
              <span>别名 ${account.alias}</span>
              <span>${account.is_member ? `会员到期 ${account.subscription_until_text || "N/A"}` : `套餐 ${String(account.plan || "N/A").replace(/^./, c => c.toUpperCase())}`}</span>
            </div>
            <div class="divider"></div>
            ${usageBlock(limit, account.plan)}
            <div class="tools">
              <button class="mini-btn green" onclick='switchAccount(${aliasArg})'>切换</button>
              <button class="mini-btn" onclick='refreshAccount(${aliasArg})'>刷新</button>
              <button class="mini-btn" onclick='refreshAccountPrecise(${aliasArg})'>精准</button>
              <button class="mini-btn blue" onclick='refreshAccountToken(${aliasArg})'>Token</button>
              <button class="mini-btn red" onclick='removeAccount(${aliasArg})'>删除</button>
            </div>
          </article>
        `;
      }).join("");
    }

    async function request(path, method = "GET", payload = null) {
      const response = await fetch(path, {
        method,
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: payload ? JSON.stringify(payload) : null
      });
      const text = await response.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (error) {
        throw new Error(text ? `服务返回了非 JSON 内容：${text.slice(0, 120)}` : "服务没有返回 JSON 内容");
      }
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || "请求失败");
      }
      return data;
    }

    async function loadState(message = "已加载", notify = true) {
      const data = await request("/api/state");
      updateEmailMaskButton();
      updateKeepaliveButton(data.settings || {});
      updateDetachedAlert(data);
      renderSummary(data);
      renderCards(data);
      if (notify) {
        setStatus(`${message} · 当前共 ${data.accounts.length} 个账号`);
      }
      return data;
    }

    async function toggleEmailMask() {
      maskEmailsEnabled = !maskEmailsEnabled;
      try {
        const data = await request("/api/state");
        updateEmailMaskButton();
        updateKeepaliveButton(data.settings || {});
        renderSummary(data);
        renderCards(data);
        setStatus(maskEmailsEnabled ? "已隐藏账户邮箱" : "已显示完整邮箱");
      } catch (error) {
        setStatus(`切换邮箱显示失败：${error.message}`);
      }
    }

    async function toggleKeepalive() {
      const target = !keepaliveEnabled;
      try {
        const result = await request("/api/settings/token-keepalive", "POST", { enabled: target });
        keepaliveEnabled = Boolean(result.settings && result.settings.token_keepalive_enabled);
        updateKeepaliveButton(result.settings || {});
        await loadState("已更新页面", false);
        setStatus(keepaliveEnabled ? "已开启保活刷新" : "已关闭保活刷新");
      } catch (error) {
        setStatus(`切换保活刷新失败：${error.message}`);
      }
    }

    function refreshWarningText(precise) {
      return precise
        ? "精准刷新会逐个账号直连官网接口获取最新额度，数据更实时，但速度更慢，也更容易触发官网限制。请不要频繁点击。"
        : "刷新全部会同步全部账号的额度视图，速度更快，适合日常查看。请不要频繁点击，以免可能触发官网限制。";
    }

    function buildRiskTip(precise) {
      const wrap = document.createElement("div");
      wrap.className = "dialog-tip";
      wrap.innerHTML = `
        <strong>${precise ? "精准刷新风险提示" : "刷新全部风险提示"}</strong>
        <ul>
          <li>不要频繁刷新，以免可能触发官网限制。</li>
          <li>${precise ? "精准刷新会逐个账号直连官网接口，数据更实时，但耗时更长。" : "刷新全部会先同步当前状态，再更新全部账号视图，速度更快。"}</li>
          <li>建议只在数据明显过期、切换账号后或需要核对最新额度时使用。</li>
        </ul>
      `;
      return wrap;
    }

    function buildDialogHero(precise) {
      const wrap = document.createElement("div");
      wrap.className = "dialog-hero";
      wrap.innerHTML = `
        <strong>${precise ? "将执行精准刷新" : "将执行普通刷新"}</strong>
        <span>${precise ? "精准刷新会逐个账号调用官网接口，数据更准，但更慢，也更容易触发限制。" : "普通刷新会先同步当前状态，再统一更新全部账户，速度更快。"}<br>如果只是日常查看，优先使用普通刷新。</span>
      `;
      return wrap;
    }

    function encodeAlias(alias) {
      return JSON.stringify(String(alias ?? ""));
    }

    async function refreshSingleAccount(alias, precise) {
      try {
        setStatus(`${precise ? "正在精准刷新" : "正在刷新"}：${alias}`);
        const state = await request("/api/state");
        const current = state.accounts.find(item => item.is_current);
        const endpoint = !precise && current && String(current.alias) === String(alias)
          ? "/api/refresh"
          : "/api/refresh-precise";
        if (endpoint === "/api/refresh") {
          await request(endpoint, "POST");
        } else {
          await request(endpoint, "POST", { alias });
        }
        await loadState("已更新页面", false);
        setStatus(`${precise ? "精准刷新" : "刷新"}完成：${alias}`);
      } catch (error) {
        setStatus(`${precise ? "精准刷新" : "刷新"}失败：${alias} / ${error.message}`);
      }
    }

    function refreshAccount(alias) {
      return refreshSingleAccount(alias, false);
    }

    function refreshAccountPrecise(alias) {
      return refreshSingleAccount(alias, true);
    }

    async function refreshAccountToken(alias) {
      try {
        setStatus(`Refreshing token: ${alias}`);
        await request("/api/refresh-token", "POST", { alias });
        await loadState("Token refreshed", false);
        setStatus(`Token refreshed: ${alias}`);
      } catch (error) {
        setStatus(`Token refresh failed: ${alias} / ${error.message}`);
      }
    }

    function refreshAllAccounts(precise = false) {
      const body = document.createElement("div");
      body.appendChild(buildDialogHero(precise));
      body.appendChild(buildRiskTip(precise));
      openDialog({
        title: precise ? "确认精准刷新全部账户" : "确认刷新全部账户",
        text: refreshWarningText(precise),
        body,
        actions: [
          { label: "先取消", onClick: closeDialog },
          {
            label: precise ? "确认精准刷新" : "确认刷新全部",
            className: "blue",
            onClick: async () => {
              closeDialog();
              try {
                setStatus(precise ? "正在精准刷新全部账户..." : "正在刷新全部账户...");
                const result = precise
                  ? await request("/api/refresh-all-precise", "POST")
                  : await request("/api/refresh-all", "POST");
                await loadState("已更新页面", false);
                const total = Number(result.total || 0);
                const refreshed = Number(result.refreshed || 0);
                const failed = Number(result.failed || 0);
                const suffix = total ? `：成功 ${refreshed}/${total}${failed ? `，失败 ${failed}` : ""}` : "";
                setStatus(`${precise ? "精准刷新" : "刷新"}全部账户完成${suffix}`);
              } catch (error) {
                setStatus(`${precise ? "精准刷新" : "刷新"}全部账户失败：${error.message}`);
              }
            }
          }
        ]
      });
    }

    function addAccount() {
      const input = document.createElement("input");
      input.placeholder = "例如：work / gmail / h10";
      openDialog({
        title: "添加当前账号",
        text: "输入一个易识别的别名；如果别名已存在，系统会用当前登录信息覆盖更新。",
        body: input,
        actions: [
          { label: "取消", onClick: closeDialog },
          {
            label: "确认添加",
            className: "blue",
            onClick: async () => {
              const alias = input.value.trim();
              if (!alias) {
                input.focus();
                return;
              }
              closeDialog();
              try {
                setStatus("正在保存当前账号...");
                await request("/api/add", "POST", { alias });
                setStatus(`已保存 ${alias}，正在刷新页面...`);
                try {
                  await loadState("已更新页面", false);
                  setStatus(`已保存 ${alias}`);
                } catch (error) {
                  setStatus(`已保存 ${alias}，但页面刷新失败：${error.message}`);
                }
              } catch (error) {
                setStatus(`保存失败：${error.message}`);
              }
            }
          }
        ]
      });
      setTimeout(() => input.focus(), 20);
    }

    function removeAccount(alias) {
      openDialog({
        title: "删除账号",
        text: `确认删除账号 ${alias} 吗？这会移除本地保存的认证备份。`,
        actions: [
          { label: "取消", onClick: closeDialog },
          {
            label: "确认删除",
            className: "red",
            onClick: async () => {
              closeDialog();
              try {
                setStatus(`正在删除 ${alias}...`);
                await request("/api/remove", "POST", { alias });
                await loadState("已更新页面", false);
                setStatus(`已删除 ${alias}`);
              } catch (error) {
                setStatus(`删除失败：${error.message}`);
              }
            }
          }
        ]
      });
    }

    async function switchAccount(alias) {
      try {
        setStatus(`正在切换到 ${alias}...`);
        await request("/api/switch", "POST", { alias });
        await loadState("已更新页面", false);
        setStatus(`已切换到 ${alias}`);
        setTimeout(() => window.location.reload(), 900);
      } catch (error) {
        setStatus(`切换失败：${error.message}`);
      }
    }

    function switchDefault() {
      openDialog({
        title: "切换默认环境",
        text: "确认切换到默认干净环境吗？切换后可以重新登录新账号。",
        actions: [
          { label: "取消", onClick: closeDialog },
          {
            label: "确认切换",
            className: "red",
            onClick: async () => {
              closeDialog();
              try {
                setStatus("正在切换到默认环境...");
                await request("/api/default", "POST");
                await loadState("已更新页面", false);
                setStatus("默认环境已就绪");
              } catch (error) {
                setStatus(`切换失败：${error.message}`);
              }
            }
          }
        ]
      });
    }

    function shutdownPanel() {
      openDialog({
        title: "关闭服务",
        text: "确认关闭当前本地页面服务吗？关闭后本页面将不可继续操作。",
        actions: [
          { label: "取消", onClick: closeDialog },
          {
            label: "确认关闭",
            className: "red",
            onClick: async () => {
              closeDialog();
              setStatus("正在关闭服务...");
              try {
                await request("/api/shutdown", "POST");
              } catch (error) {
              }
              setStatus("服务已停止，可以关闭当前页面。");
              setTimeout(() => window.close(), 900);
            }
          }
        ]
      });
    }

    loadState().catch(error => setStatus(`加载失败：${error.message}`));
  </script>
</body>
</html>
"""


class DashboardState:
    def __init__(self, service: CodexService | None = None):
        self.service = service or CodexService()

    @staticmethod
    def _fallback_limits(account):
        limits = []
        reset_map = {}
        for part in str(account.get("reset_at") or "").split(" / "):
            if ":" not in part:
                continue
            label, value = part.split(":", 1)
            reset_map[label.strip()] = value.strip()
        for part in str(account.get("usage_left") or "").split(" / "):
            if ":" not in part:
                continue
            label, value = part.split(":", 1)
            try:
                left_percent = float(value.strip().replace("%", ""))
            except Exception:
                continue
            limits.append(
                {
                    "label": label.strip(),
                    "left_percent": left_percent,
                    "reset_at": reset_map.get(label.strip(), "N/A"),
                }
            )
        return limits

    def snapshot(self):
        snapshot = self.service.build_dashboard_snapshot(include_live_current_snapshot=False)
        accounts = snapshot.get("accounts")
        if not isinstance(accounts, list):
            snapshot["accounts"] = []
            accounts = snapshot["accounts"]
        for item in accounts:
            if not isinstance(item, dict):
                continue
            item["usage_limits"] = item.get("usage_limits") or self._fallback_limits(item)
        return snapshot


def _json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler):
    body = HTML.encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_handler(app, server_ref):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            try:
                if self.path in ("/", "/index.html"):
                    _html_response(self)
                    return
                if self.path == "/api/state":
                    _json_response(self, HTTPStatus.OK, app.snapshot())
                    return
                _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            except Exception as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

            try:
                if self.path == "/api/refresh":
                    app.service.refresh_current_account_usage_snapshot(wait_seconds=1.0, retries=1)
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    return
                if self.path == "/api/refresh-all":
                    result = app.service.refresh_all_accounts_local_snapshot(wait_seconds=1.0, retries=1)
                    _json_response(self, HTTPStatus.OK, result)
                    return
                if self.path == "/api/refresh-precise":
                    alias = str(payload.get("alias") or "").strip()
                    if not alias:
                        raise ValueError("Alias is required")
                    snapshot = app.service.refresh_precise_usage_for_alias(alias)
                    _json_response(self, HTTPStatus.OK, {"ok": True, "snapshot": snapshot})
                    return
                if self.path == "/api/refresh-token":
                    alias = str(payload.get("alias") or "").strip()
                    if not alias:
                        raise ValueError("Alias is required")
                    result = app.service.refresh_access_token_for_alias(alias)
                    _json_response(self, HTTPStatus.OK, result)
                    return
                if self.path == "/api/refresh-all-precise":
                    result = app.service.refresh_all_accounts_precise(throttle_seconds=1.0)
                    _json_response(self, HTTPStatus.OK, result)
                    return
                if self.path == "/api/settings/token-keepalive":
                    enabled = bool(payload.get("enabled"))
                    settings = app.service.set_token_keepalive_enabled(enabled)
                    _json_response(self, HTTPStatus.OK, {"ok": True, "settings": settings})
                    return
                if self.path == "/api/add":
                    alias = str(payload.get("alias") or "").strip()
                    if not alias:
                        raise ValueError("Alias is required")
                    if not app.service.add_account(alias):
                        raise ValueError("Add account failed")
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    return
                if self.path == "/api/remove":
                    alias = str(payload.get("alias") or "").strip()
                    if not alias or not app.service.remove_account(alias):
                        raise ValueError("Remove account failed")
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    return
                if self.path == "/api/switch":
                    alias = str(payload.get("alias") or "").strip()
                    if not alias or not app.service.switch_account(alias):
                        raise ValueError("Switch account failed")
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    return
                if self.path == "/api/default":
                    app.service.switch_to_default()
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    return
                if self.path == "/api/shutdown":
                    _json_response(self, HTTPStatus.OK, {"ok": True})
                    threading.Thread(target=server_ref.shutdown, daemon=True).start()
                    return
                _json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            except Exception as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    return Handler


def _open_browser(url: str) -> None:
    try:
        if webbrowser.open(url, new=1):
            return
    except Exception:
        pass

    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        opener = "open" if platform.system() == "Darwin" else "xdg-open"
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def launch_dashboard() -> None:
    app = DashboardState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)
    server.RequestHandlerClass = _make_handler(app, server)
    url = f"http://127.0.0.1:{server.server_port}/"
    threading.Timer(0.35, lambda: _open_browser(url)).start()
    print(f"Launching web UI at {url}")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
