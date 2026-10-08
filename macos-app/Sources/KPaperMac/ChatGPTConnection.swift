import AppKit
import Foundation
import SwiftUI

struct ChatGPTAccount: Codable, Identifiable {
    let id: String
    let label: String
    let email: String
    let signed_in: Bool
    let plan_enabled: Bool
}

struct ChatGPTModelChoice: Codable, Identifiable {
    let id: String
    let display_name: String
}

private struct ChatGPTConnectionResult: Decodable {
    let ok: Bool
    let profiles: [ChatGPTAccount]?
    let active: ChatGPTAccount?
    let models: [ChatGPTModelChoice]?
    let message: String?
    let revocation_confirmed: Bool?
    let usage: ChatGPTUsageSummary?
    let usage_message: String?
    let weekly_usage: ChatGPTWeeklyUsage?
}

@MainActor
final class ChatGPTConnection: ObservableObject {
    @Published private(set) var accounts: [ChatGPTAccount] = []
    @Published private(set) var active: ChatGPTAccount?
    @Published private(set) var models: [ChatGPTModelChoice] = []
    @Published private(set) var busy = false
    @Published private(set) var usage: ChatGPTUsageSummary?
    @Published private(set) var usageBusy = false
    @Published private(set) var usageMessage = ""
    @Published private(set) var weeklyUsage: ChatGPTWeeklyUsage?
    @Published private(set) var weeklyBusy = false
    @Published private(set) var weeklyMessage = ""
    private var weeklyAttemptAt: Date?
    @Published private(set) var message = "Continue with ChatGPT로 연결해주세요."
    @Published var showWelcome = false
    private var process: Process?

    var planEnabled: Bool { active?.plan_enabled == true }

    func refresh(repoPath: String) {
        perform("status", repoPath: repoPath)
    }

    func signIn(repoPath: String, newAccount: Bool = false) {
        perform("login", repoPath: repoPath, profile: newAccount ? nil : active?.id)
    }

    func select(_ id: String, repoPath: String) {
        perform("select", repoPath: repoPath, profile: id)
    }

    func signOut(repoPath: String) {
        perform("logout", repoPath: repoPath)
    }

    func refreshUsage(repoPath: String) {
        guard !busy, !usageBusy, let profileID = active?.id else { return }
        usageBusy = true
        Task {
            defer { usageBusy = false }
            do {
                let result = try await run("usage", repoPath: repoPath, profile: profileID, cancellable: false)
                guard active?.id == profileID, !busy else { return }
                usage = result.usage
                usageMessage = result.usage_message ?? ""
            } catch {
                guard active?.id == profileID, !busy else { return }
                usageMessage = "사용량을 갱신하지 못했습니다. 새로고침으로 다시 시도해주세요."
            }
        }
    }

    func refreshWeeklyUsage(repoPath: String, force: Bool = false) {
        guard !busy, !weeklyBusy, planEnabled, let profileID = active?.id else { return }
        if !force, let attempted = weeklyAttemptAt, Date().timeIntervalSince(attempted) < 60 { return }
        weeklyAttemptAt = Date()
        weeklyBusy = true
        Task {
            defer { weeklyBusy = false }
            do {
                let result = try await run("weekly", repoPath: repoPath, profile: profileID, cancellable: false)
                guard active?.id == profileID, !busy else { return }
                weeklyUsage = result.weekly_usage
                weeklyMessage = ""
            } catch {
                guard active?.id == profileID, !busy else { return }
                weeklyMessage = "주간 한도 갱신 실패"
            }
        }
    }

    func cancelLogin() {
        if let process { TranslatorModel.terminateProcessTree(process) }
    }

    private func perform(_ action: String, repoPath: String, profile: String? = nil) {
        guard !busy else { return }
        busy = true
        if action != "status" {
            usage = nil; usageMessage = ""
            weeklyUsage = nil; weeklyMessage = ""; weeklyAttemptAt = nil
        }
        if action == "login" { message = "브라우저에서 로그인과 요금제 사용 동의를 완료해주세요." }
        Task {
            defer { busy = false }
            do {
                let result = try await run(action, repoPath: repoPath, profile: profile)
                if active?.id != result.active?.id {
                    weeklyUsage = nil; weeklyMessage = ""; weeklyAttemptAt = nil
                }
                accounts = result.profiles ?? []
                active = result.active
                usage = result.usage
                usageMessage = result.usage_message ?? ""
                models = []  // Never reuse the previous account's model catalog.
                message = active?.signed_in == true
                    ? (planEnabled ? "ChatGPT 요금제 사용이 연결되었습니다." : "로그인됨 · 요금제 사용 권한을 허용해주세요.")
                    : "Continue with ChatGPT로 연결해주세요."
                if result.revocation_confirmed == false {
                    message = "이 Mac에서 로그아웃했습니다. 원격 세션 해제는 확인되지 않아 ChatGPT 설정에서 연결을 해제해주세요."
                }
                if action == "login", planEnabled, !UserDefaults.standard.bool(forKey: "chatgptPlanWelcomeShown") {
                    showWelcome = true
                    UserDefaults.standard.set(true, forKey: "chatgptPlanWelcomeShown")
                }
                if planEnabled {
                    let catalog = try await run("models", repoPath: repoPath)
                    models = catalog.models ?? []
                    if models.isEmpty { message = "이 계정에서 사용할 수 있는 모델이 없습니다." }
                }
            } catch {
                message = error.localizedDescription
            }
        }
    }

    private func run(_ action: String, repoPath: String, profile: String? = nil, cancellable: Bool = true) async throws -> ChatGPTConnectionResult {
        guard let uv = TranslatorModel.resolveUVExecutable() else {
            throw NSError(domain: "KPaper", code: 1, userInfo: [NSLocalizedDescriptionKey: "uv를 찾을 수 없습니다."])
        }
        return try await withCheckedThrowingContinuation { continuation in
            let job = Process()
            job.currentDirectoryURL = URL(fileURLWithPath: repoPath)
            job.executableURL = uv
            job.arguments = ["run", "scripts/kpaper.py", "chatgpt", action, "--json"] + (profile.map { ["--profile", $0] } ?? [])
            var environment = TranslatorModel.environmentByAddingToolDirectories(ProcessInfo.processInfo.environment, tools: [uv])
            environment["UV_CACHE_DIR"] = ".uv-cache"
            job.environment = environment
            let output = Pipe(), errors = Pipe()
            job.standardOutput = output
            job.standardError = errors
            if cancellable { self.process = job }
            DispatchQueue.global(qos: .userInitiated).async {
                // Drain both pipes concurrently so dependency diagnostics cannot block login.
                do {
                    try job.run()
                    let group = DispatchGroup()
                    group.enter()
                    DispatchQueue.global(qos: .utility).async {
                        _ = errors.fileHandleForReading.readDataToEndOfFile()
                        group.leave()
                    }
                    let data = output.fileHandleForReading.readDataToEndOfFile()
                    job.waitUntilExit()
                    group.wait()
                    let result = try JSONDecoder().decode(ChatGPTConnectionResult.self, from: data)
                    guard job.terminationStatus == 0, result.ok else {
                        throw NSError(domain: "KPaper.ChatGPT", code: Int(job.terminationStatus), userInfo: [
                            NSLocalizedDescriptionKey: result.message ?? "ChatGPT 로그인 연결을 완료하지 못했습니다."])
                    }
                    continuation.resume(returning: result)
                } catch {
                    continuation.resume(throwing: error)
                }
                DispatchQueue.main.async { if self.process === job { self.process = nil } }
            }
        }
    }
}

struct ChatGPTConnectionSettings: View {
    @EnvironmentObject private var connection: ChatGPTConnection
    @EnvironmentObject private var settings: TranslatorModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !connection.accounts.isEmpty {
                Picker("ChatGPT 계정", selection: Binding(
                    get: { connection.active?.id ?? "" },
                    set: { connection.select($0, repoPath: settings.repoPath) }
                )) {
                    ForEach(connection.accounts) { account in Text(account.label).tag(account.id) }
                }
                .labelsHidden()
                .disabled(connection.busy || settings.isRunning)
            }
            Text(connection.message).font(.system(size: 12)).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                Button("Continue with ChatGPT") { connection.signIn(repoPath: settings.repoPath) }
                    .buttonStyle(.borderedProminent)
                if !connection.accounts.isEmpty {
                    Button("계정 추가") { connection.signIn(repoPath: settings.repoPath, newAccount: true) }
                    Button("로그아웃") { connection.signOut(repoPath: settings.repoPath) }
                        .disabled(connection.active?.signed_in != true)
                }
                Link("사용량 관리", destination: URL(string: "https://chatgpt.com/settings/usage")!)
            }
            .disabled(connection.busy || settings.isRunning)
            if connection.busy {
                HStack {
                    ProgressView().controlSize(.small)
                    Button("취소") { connection.cancelLogin() }
                }
            }
        }
        .disabled(settings.isRunning)
        .task { connection.refresh(repoPath: settings.repoPath) }
        .onReceive(NotificationCenter.default.publisher(for: .init("KPaperRefreshChatGPT"))) { _ in
            connection.refresh(repoPath: settings.repoPath)
        }
        .alert("ChatGPT 요금제를 사용합니다", isPresented: $connection.showWelcome) {
            Button("확인", role: .cancel) { }
            Button("사용량 관리") { NSWorkspace.shared.open(URL(string: "https://chatgpt.com/settings/usage")!) }
        } message: {
            Text("KPaper의 번역과 논문 질문은 연결한 ChatGPT 요금제의 사용량에 포함됩니다. ChatGPT 설정에서 앱 사용량과 한도를 관리할 수 있습니다.")
        }
        .onChange(of: connection.models.map(\.id)) { ids in
            guard !ids.isEmpty else { return }
            if !ids.contains(settings.selectedChatGPTModel) {
                settings.selectedChatGPTModel = ids.contains("gpt-6-luna") ? "gpt-6-luna" : ids[0]
                settings.saveSettings()
            }
        }
    }
}
