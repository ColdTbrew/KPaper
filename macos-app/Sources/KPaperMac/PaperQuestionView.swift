import SwiftUI
import Foundation

struct PaperCitation: Codable, Identifiable {
    let id: String
    let label: String
}

struct PaperQuestionMessage: Codable, Identifiable {
    var id = UUID()
    let role: String
    let content: String
    var citations: [PaperCitation] = []
}

private struct PaperQuestionResponse: Decodable {
    let answer: String
    let citations: [PaperCitation]
}

private final class QuestionProcessOutput: @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()
    func append(_ data: Data) { lock.lock(); bytes.append(data); lock.unlock() }
    var data: Data { lock.lock(); defer { lock.unlock() }; return bytes }
}

@MainActor
final class PaperQuestionModel: ObservableObject {
    @Published var messages: [PaperQuestionMessage] = []
    @Published var question = ""
    @Published var isRunning = false
    @Published var error: String?
    private var documentURL: URL?
    private var process: Process?
    private var requestID: UUID?
    private var storageKey: String { "paper.questions." + (documentURL?.standardizedFileURL.path ?? "") }

    func open(_ url: URL) {
        guard documentURL != url else { return }
        cancel()
        documentURL = url
        question = ""
        error = nil
        messages = UserDefaults.standard.data(forKey: storageKey).flatMap { try? JSONDecoder().decode([PaperQuestionMessage].self, from: $0) } ?? []
    }
    private func save() {
        if let data = try? JSONEncoder().encode(messages) { UserDefaults.standard.set(data, forKey: storageKey) }
    }
    func clearConversation() {
        guard !isRunning else { return }
        messages = []
        error = nil
        save()
    }
    func cancel() {
        requestID = nil
        if let process, process.isRunning {
            let children = Process()
            children.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
            children.arguments = ["-TERM", "-P", String(process.processIdentifier)]
            try? children.run()
            process.terminate()
        }
        process = nil
        isRunning = false
    }
    func send(repoPath: String) {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isRunning, let documentURL else { return }
        guard let uv = TranslatorModel.resolveUVExecutable() else {
            error = "uv를 찾을 수 없습니다. 설치 및 프로젝트 경로를 확인하세요."
            return
        }
        let history = messages.map { ["role": $0.role, "content": $0.content] }
        let request = ["question": text, "history": history, "model": "gpt-6-luna"] as [String: Any]
        let requestURL = FileManager.default.temporaryDirectory.appendingPathComponent("kpaper-question-\(UUID().uuidString).json")
        do { try JSONSerialization.data(withJSONObject: request).write(to: requestURL, options: .atomic) }
        catch { self.error = "질문을 준비하지 못했습니다: \(error.localizedDescription)"; return }
        messages.append(PaperQuestionMessage(role: "user", content: text))
        save()
        question = ""
        error = nil
        isRunning = true
        let ticket = UUID()
        requestID = ticket
        let job = Process()
        job.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        job.executableURL = uv
        job.arguments = ["run", "python", "scripts/paper_qa.py", "--input", documentURL.path, "--request", requestURL.path]
        var tools = [uv]
        if let codex = TranslatorModel.resolveCodexExecutable() { tools.append(codex) }
        var environment = TranslatorModel.environmentByAddingToolDirectories(ProcessInfo.processInfo.environment, tools: tools)
        environment["UV_CACHE_DIR"] = ".uv-cache"
        job.environment = environment
        let stdout = Pipe(), stderr = Pipe()
        job.standardOutput = stdout
        job.standardError = stderr
        process = job
        do { try job.run() }
        catch {
            process = nil
            requestID = nil
            isRunning = false
            self.error = "질문 실행 실패: \(error.localizedDescription)"
            question = text
            try? FileManager.default.removeItem(at: requestURL)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            defer { try? FileManager.default.removeItem(at: requestURL) }
                let result = QuestionProcessOutput(), failure = QuestionProcessOutput()
                let readers = DispatchGroup()
                readers.enter()
                DispatchQueue.global().async { result.append(stdout.fileHandleForReading.readDataToEndOfFile()); readers.leave() }
                readers.enter()
                DispatchQueue.global().async { failure.append(stderr.fileHandleForReading.readDataToEndOfFile()); readers.leave() }
                job.waitUntilExit()
                readers.wait()
                let response = job.terminationStatus == 0 ? try? JSONDecoder().decode(PaperQuestionResponse.self, from: result.data) : nil
                let detail = String(data: failure.data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                Task { @MainActor in
                    guard self.requestID == ticket else { return }
                    self.process = nil
                    self.requestID = nil
                    self.isRunning = false
                    if let response {
                        self.messages.append(PaperQuestionMessage(role: "assistant", content: response.answer, citations: response.citations))
                        self.save()
                    } else {
                        self.error = detail.isEmpty ? "답변을 받지 못했습니다. Codex 로그인과 연결 상태를 확인하세요." : String(detail.suffix(1600))
                        self.question = text
                    }
                }
        }
    }
}

struct PaperQuestionView: View {
    let url: URL
    let repoPath: String
    let jump: (String) -> Void
    @StateObject private var model = PaperQuestionModel()
    @FocusState private var composerFocused: Bool
    @State private var clearConfirmation = false
    private let suggestions = ["이 논문의 핵심 기여를 설명해줘", "실험 결과와 한계는 무엇이야?", "방법을 단계별로 설명해줘"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Label("논문에 질문", systemImage: "text.bubble").font(.system(size: 14, weight: .semibold))
                    Spacer()
                    Button { clearConfirmation = true } label: { Image(systemName: "trash") }
                        .buttonStyle(.borderless)
                        .frame(width: 28, height: 28)
                        .help("이 논문의 대화 지우기")
                        .accessibilityLabel("이 논문의 대화 지우기")
                        .disabled(model.isRunning || model.messages.isEmpty)
                }
                Text("현재 논문을 근거로 답변합니다.").font(.caption).foregroundStyle(.secondary)
                Text("질문과 논문 내용이 Codex로 전송됩니다.").font(.system(size: 10)).foregroundStyle(.secondary)
            }.padding(16)
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        if model.messages.isEmpty {
                            Text("궁금한 내용을 질문해 보세요.").font(.system(size: 13)).foregroundStyle(.secondary)
                            ForEach(suggestions, id: \.self) { suggestion in
                                Button(suggestion) { model.question = suggestion; composerFocused = true }
                                    .buttonStyle(.bordered)
                                    .multilineTextAlignment(.leading)
                            }
                        }
                        ForEach(model.messages) { message in
                            VStack(alignment: .leading, spacing: 9) {
                                Text(message.role == "user" ? "내 질문" : "논문 답변")
                                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                                Text((try? AttributedString(markdown: message.content, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(message.content))
                                    .font(.system(size: 13)).textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                ForEach(message.citations) { citation in
                                    Button { jump(citation.id) } label: {
                                        Label(citation.label, systemImage: "arrow.up.right").font(.caption).multilineTextAlignment(.leading)
                                    }.buttonStyle(.bordered).help("근거 문단으로 이동")
                                }
                            }
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(message.role == "user" ? Color.accentColor.opacity(0.06) : Color(nsColor: .textBackgroundColor))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .id(message.id)
                        }
                        if model.isRunning { HStack { ProgressView().controlSize(.small); Text("논문을 읽고 답변하는 중…").font(.caption) } }
                    }.padding(14)
                }
                .onChange(of: model.messages.count) { _ in
                    if let last = model.messages.last { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 9) {
                if let error = model.error {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.red).textSelection(.enabled)
                        .lineLimit(5)
                }
                TextField("논문에 대해 질문하세요…", text: $model.question, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...5)
                    .focused($composerFocused)
                    .accessibilityLabel("논문 질문")
                    .onSubmit { model.send(repoPath: repoPath) }
                HStack {
                    Text("gpt-6-luna · low").font(.system(size: 10)).foregroundStyle(.secondary)
                    Spacer()
                    if model.isRunning {
                        Button("취소") { model.cancel() }.buttonStyle(.bordered)
                    } else {
                        Button("질문 보내기") { model.send(repoPath: repoPath) }
                            .buttonStyle(.borderedProminent)
                            .disabled(model.question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }.padding(14)
        }
        .frame(width: 360)
        .background(Color(nsColor: .controlBackgroundColor))
        .onAppear { model.open(url) }
        .onChange(of: url) { model.open($0) }
        .onDisappear { model.cancel() }
        .confirmationDialog("이 논문의 대화를 지울까요?", isPresented: $clearConfirmation) {
            Button("대화 지우기", role: .destructive) { model.clearConversation() }
            Button("취소", role: .cancel) { }
        }
    }
}
