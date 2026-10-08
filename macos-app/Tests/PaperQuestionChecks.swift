import Foundation
import SwiftUI

// Compile with PaperQuestionView.swift, without the application entry point.
enum TranslationProvider { case chatgpt, codex, api }
final class TranslatorModel: ObservableObject {
    var selectedProvider: TranslationProvider = .codex
    var selectedChatGPTModel = "gpt-6-luna"
    static func resolveUVExecutable() -> URL? { URL(fileURLWithPath: ProcessInfo.processInfo.environment["QA_TEST_UV"]!) }
    static func resolveCodexExecutable() -> URL? { nil }
    static func environmentByAddingToolDirectories(_ environment: [String: String], tools: [URL]) -> [String: String] { environment }
}

@main
struct PaperQuestionChecks {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("question-check-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = root.appendingPathComponent("first.html"), second = root.appendingPathComponent("second.html")
        let model = PaperQuestionModel()
        model.open(first)
        model.question = "핵심 기여는?"
        model.send(repoPath: root.path)
        for _ in 0..<100 where model.isRunning { try await Task.sleep(nanoseconds: 30_000_000) }
        precondition(!model.isRunning && model.error == nil, model.error ?? "runner timed out")
        precondition(model.messages.count == 2)
        precondition(model.messages.last?.content == "**검증된 답변**")
        precondition(model.messages.last?.citations.first?.id == "p1")
        model.open(second)
        precondition(model.messages.isEmpty, "Conversations must be isolated by document")
        model.open(first)
        precondition(model.messages.count == 2, "Conversation should survive reentry")
        model.question = "취소 테스트"
        model.send(repoPath: root.path)
        model.cancel()
        try await Task.sleep(nanoseconds: 150_000_000)
        precondition(!model.isRunning)
        precondition(model.messages.count == 3, "Cancelled request must not append a late answer")
        model.clearConversation()
        precondition(model.messages.isEmpty)
        model.open(second)
        model.open(first)
        precondition(model.messages.isEmpty, "Cleared conversation must stay empty after reentry")
        UserDefaults.standard.removeObject(forKey: "paper.questions." + first.standardizedFileURL.path)
        print("Paper question process, persistence, citations and cancellation checks passed")
    }
}
