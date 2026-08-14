import AppKit
import Network
import SwiftUI
import UniformTypeIdentifiers
import WebKit

@main
struct KPaperMacApp: App {
    @StateObject private var model = TranslatorModel()

    var body: some Scene {
        WindowGroup {
            WorkspaceView()
                .environmentObject(model)
                .frame(minWidth: 820, minHeight: 640)
        }
        .windowStyle(.hiddenTitleBar)
    }
}

private enum WorkspaceStage {
    case importDocument
    case translating
    case reader
}

private enum ImportMode: String, CaseIterable, Identifiable {
    case web = "웹 링크"
    case pdf = "PDF 파일"

    var id: String { rawValue }
}

private enum SidebarDestination {
    case translation
    case recent
    case documents
    case settings
}

struct TranslationJob: Identifiable {
    let id: UUID
    let paperID: String
    let title: String
    var statusText: String
    var isRunning: Bool
    var progressCompleted: Int = 0
    var progressTotal: Int = 0
    var progressLabel: String = "준비 중"
    var progressPhase: String = "preparing"
    var progressDetail: String = ""
    var startedAt: Date = .now
    var logText: String = ""
}

struct WorkspaceView: View {
    @EnvironmentObject private var model: TranslatorModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var stage: WorkspaceStage = .importDocument
    @State private var destination: SidebarDestination = .translation
    @State private var importMode: ImportMode = .web
    @State private var sourceURL = ""
    @State private var isDropTargeted = false
    @State private var isFileImporterPresented = false
    @State private var readerScrollTarget: String?
    @State private var loadedReaderHeadings: [ReaderHeading] = []
    @State private var outputDocuments: [OutputDocument] = []
    @State private var documentSearchText = ""
    @State private var documentLoadError: String?
    private let readerPreviewEnabled: Bool

    init() {
        let preview = ProcessInfo.processInfo.environment["PAPER_TRANSLATOR_UI_PREVIEW"]
        let isProgressPreview = preview == "progress" || ProcessInfo.processInfo.arguments.contains("--preview-progress")
        readerPreviewEnabled = ProcessInfo.processInfo.arguments.contains("--preview-reader")
        _stage = State(initialValue: readerPreviewEnabled ? .reader : isProgressPreview ? .translating : .importDocument)
    }

    var body: some View {
        HStack(spacing: 0) {
            if !(destination == .translation && stage == .reader) {
                sidebar
                Divider()
            }
            mainContent
        }
        .background(WorkspacePalette.canvas)
        .fileImporter(
            isPresented: $isFileImporterPresented,
            allowedContentTypes: [.pdf],
            allowsMultipleSelection: false
        ) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            model.translatePDF(url)
        }
        .onChange(of: model.isRunning) { running in
            if running {
                destination = .translation
                stage = .translating
            }
        }
        .onChange(of: model.selectedWorkflowID) { _ in
            if model.selectedWorkflowIsRunning {
                destination = .translation
                stage = .translating
            }
        }
        .onChange(of: model.statusText) { status in
            if status == "완료", !model.lastPaperID.isEmpty {
                reloadOutputDocuments()
                stage = .reader
            }
        }
        .onChange(of: model.repoPath) { _ in
            if destination == .documents {
                reloadOutputDocuments()
            }
        }
        .animation(reduceMotion ? .linear(duration: 0.12) : .spring(response: 0.34, dampingFraction: 0.92), value: stage)
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 8) {
            SidebarButton(title: "새 번역", icon: "plus", isSelected: destination == .translation) {
                destination = .translation
                stage = .importDocument
                importMode = .web
                sourceURL = ""
            }
            SidebarButton(title: "최근 문서", icon: "clock", isSelected: destination == .recent) {
                destination = .recent
            }
            SidebarButton(title: "내 문서", icon: "doc", isSelected: destination == .documents) {
                destination = .documents
                reloadOutputDocuments()
            }
            SidebarButton(title: "설정", icon: "gearshape", isSelected: destination == .settings) {
                destination = .settings
            }

            if !model.runningJobs.isEmpty {
                Text("진행 중")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(WorkspacePalette.tertiaryText)
                    .padding(.horizontal, 18)
                    .padding(.top, 10)

                ForEach(model.runningJobs) { job in
                    Button {
                        model.selectWorkflow(job.id)
                        destination = .translation
                        stage = .translating
                    } label: {
                        HStack(spacing: 8) {
                            ProgressView()
                                .controlSize(.small)
                            Text(job.paperID)
                                .font(.system(size: 12, weight: .medium))
                                .lineLimit(1)
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 18)
                        .padding(.vertical, 7)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("\(job.title): \(job.progressLabel)")
                }
            }

            Spacer()

            HStack(spacing: 8) {
                Image(systemName: "questionmark.circle")
                Text("도움말")
            }
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(WorkspacePalette.secondaryText)
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .padding(.bottom, 14)
        .padding(.top, 46)
        .frame(width: 156, alignment: .topLeading)
        .background(WorkspacePalette.sidebar)
    }

    @ViewBuilder
    private var mainContent: some View {
        switch destination {
        case .translation:
            switch stage {
            case .importDocument:
                importScreen
                    .transition(.opacity.combined(with: .move(edge: .leading)))
            case .translating:
                progressScreen
                    .transition(.opacity)
            case .reader:
                readerScreen
                    .transition(.opacity.combined(with: .move(edge: .trailing)))
            }
        case .recent:
            recentScreen
        case .documents:
            documentsScreen
        case .settings:
            settingsScreen
        }
    }

    private var importScreen: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                screenHeader(
                    step: "1",
                    title: "문서 가져오기",
                    subtitle: "웹 링크 또는 PDF 파일을 추가하세요."
                )

                ImportModeSelector(selection: $importMode)

                if importMode == .web {
                    urlImportPanel
                } else {
                    pdfImportPanel
                }

                HStack(spacing: 14) {
                    LanguageMenu(title: "원본 언어", value: "자동 감지", icon: "character.book.closed")
                    LanguageMenu(title: "번역 언어", value: "한국어", icon: "globe")
                }

                Button {
                    if importMode == .web {
                        model.translateURL(sourceURL)
                    } else {
                        isFileImporterPresented = true
                    }
                } label: {
                    HStack {
                        Spacer()
                        Text(importMode == .web ? "번역 시작" : "PDF 선택")
                        Image(systemName: "arrow.right")
                        Spacer()
                    }
                }
                .buttonStyle(WorkspacePrimaryButtonStyle())
                .disabled(importMode == .web && sourceURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .frame(maxWidth: 540, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.vertical, 34)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
    }

    private var urlImportPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("논문 링크")
                .font(.system(size: 13, weight: .semibold))
            HStack(spacing: 8) {
                TextField("https://arxiv.org/abs/...", text: $sourceURL)
                    .textFieldStyle(WorkspaceTextFieldStyle())
                    .onSubmit { model.translateURL(sourceURL) }
                Button {
                    sourceURL = NSPasteboard.general.string(forType: .string) ?? ""
                } label: {
                    Image(systemName: "link")
                        .frame(width: 18, height: 18)
                }
                .buttonStyle(WorkspaceIconButtonStyle())
                .help("클립보드에서 붙여넣기")
            }

            DividerLabel(text: "또는")

            pdfDropZone(compact: true)
        }
    }

    private var pdfImportPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PDF 파일")
                .font(.system(size: 13, weight: .semibold))
            pdfDropZone(compact: false)
        }
    }

    private func pdfDropZone(compact: Bool) -> some View {
        VStack(spacing: 10) {
            Image(systemName: isDropTargeted ? "doc.fill.badge.plus" : "doc.badge.plus")
                .font(.system(size: compact ? 28 : 36, weight: .regular))
                .foregroundStyle(isDropTargeted ? WorkspacePalette.blue : WorkspacePalette.secondaryText)
            Text(isDropTargeted ? "놓아서 번역 시작" : "PDF 파일을 드래그하거나 클릭하여 선택하세요.")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(WorkspacePalette.secondaryText)
            Text("최대 200MB")
                .font(.system(size: 11))
                .foregroundStyle(WorkspacePalette.tertiaryText)
        }
        .frame(maxWidth: .infinity, minHeight: compact ? 148 : 190)
        .background(isDropTargeted ? WorkspacePalette.blue.opacity(0.07) : WorkspacePalette.controlFill)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(
                    isDropTargeted ? WorkspacePalette.blue : WorkspacePalette.border,
                    style: StrokeStyle(lineWidth: isDropTargeted ? 1.5 : 1, dash: [5, 4])
                )
        }
        .contentShape(Rectangle())
        .onTapGesture { isFileImporterPresented = true }
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isDropTargeted) { providers in
            model.handleDrop(providers: providers)
        }
        .scaleEffect(isDropTargeted ? 1.006 : 1)
        .animation(reduceMotion ? .linear(duration: 0.1) : .spring(response: 0.28, dampingFraction: 0.84), value: isDropTargeted)
        .accessibilityLabel("PDF 가져오기")
        .accessibilityHint("클릭하거나 PDF 파일을 끌어다 놓으세요")
    }

    private var progressScreen: some View {
        VStack(alignment: .leading, spacing: 28) {
            screenHeader(
                step: "2",
                title: "번역 진행 중",
                subtitle: "문서 구조를 보존하며 한국어로 번역하고 있습니다."
            )

            HStack(spacing: 14) {
                Image(systemName: "doc.text")
                    .font(.system(size: 22, weight: .regular))
                    .foregroundStyle(WorkspacePalette.secondaryText)
                    .frame(width: 48, height: 58)
                    .background(WorkspacePalette.controlFill)
                    .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(WorkspacePalette.border))
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.lastPaperID.isEmpty ? "선택한 논문" : model.lastPaperID)
                        .font(.system(size: 14, weight: .semibold))
                        .lineLimit(1)
                    Text("구조 보존 한국어 번역")
                        .font(.system(size: 12))
                        .foregroundStyle(WorkspacePalette.secondaryText)
                }
                Spacer()
            }

            HStack(spacing: 42) {
                progressRing
                progressTimeline
            }

            VStack(alignment: .leading, spacing: 12) {
                Text("구조 보존 상태")
                    .font(.system(size: 13, weight: .semibold))
                HStack(spacing: 10) {
                    PreservationCard(title: "그림", icon: "photo", state: "보존 중")
                    PreservationCard(title: "표", icon: "tablecells", state: "보존 중")
                    PreservationCard(title: "수식", icon: "function", state: "보존 중")
                    PreservationCard(title: "인용", icon: "quote.opening", state: "보존 중")
                }
            }

            Spacer()

            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.progressLabel)
                        .font(.system(size: 12, weight: .medium))
                    if !model.progressDetail.isEmpty {
                        Text(model.progressDetail)
                            .font(.system(size: 11))
                            .foregroundStyle(WorkspacePalette.secondaryText)
                    }
                }
                Spacer()
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(elapsedText(at: context.date))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(WorkspacePalette.tertiaryText)
                }
                Button("취소") { model.cancel() }
                    .buttonStyle(WorkspaceSecondaryButtonStyle())
            }
        }
        .padding(.horizontal, 42)
        .padding(.vertical, 34)
    }

    private var progressRing: some View {
        ZStack {
            Circle()
                .stroke(WorkspacePalette.blue.opacity(0.10), lineWidth: 9)
            Circle()
                .trim(from: 0, to: max(model.progressFraction, model.progressTotal == 0 ? 0.08 : 0))
                .stroke(WorkspacePalette.blue, style: StrokeStyle(lineWidth: 9, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 4) {
                Text(model.progressTotal > 0 ? "\(Int(model.progressFraction * 100))%" : "…")
                    .font(.system(size: 32, weight: .semibold, design: .rounded))
                    .tracking(-1)
                Text(progressPhaseTitle)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(WorkspacePalette.secondaryText)
            }
        }
        .frame(width: 150, height: 150)
        .animation(reduceMotion ? .linear(duration: 0.1) : .spring(response: 0.4, dampingFraction: 1), value: model.progressFraction)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(progressPhaseTitle) 진행률")
        .accessibilityValue(model.progressTotal > 0 ? "\(Int(model.progressFraction * 100))퍼센트" : "준비 중")
    }

    private func elapsedText(at date: Date) -> String {
        let seconds = max(0, Int(date.timeIntervalSince(model.workflowStartedAt)))
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }

    private var progressTimeline: some View {
        VStack(alignment: .leading, spacing: 0) {
            TimelineRow(title: "PDF 검사", detail: timelineDetail(for: 0), state: timelineState(for: 0))
            TimelineRow(title: "구조 추출", detail: timelineDetail(for: 1), state: timelineState(for: 1))
            TimelineRow(title: "번역", detail: timelineDetail(for: 2), state: timelineState(for: 2))
            TimelineRow(title: "문서 스타일 적용", detail: timelineDetail(for: 3), state: timelineState(for: 3), showsLine: false)
        }
        .frame(maxWidth: 280)
    }

    private var progressPhaseRank: Int {
        switch model.progressPhase {
        case "inspect", "preparing": return 0
        case "layout": return 1
        case "translate": return 2
        case "restyle": return 3
        case "complete": return 4
        default: return 0
        }
    }

    private var progressPhaseTitle: String {
        switch model.progressPhase {
        case "inspect": return "PDF 검사"
        case "layout": return "구조 추출"
        case "translate": return "번역 중"
        case "restyle": return "마무리"
        case "complete": return "완료"
        default: return "준비 중"
        }
    }

    private func timelineState(for rank: Int) -> TimelineState {
        if rank < progressPhaseRank { return .complete }
        if rank == progressPhaseRank && model.progressPhase != "complete" { return .current }
        return .pending
    }

    private func timelineDetail(for rank: Int) -> String {
        if rank < progressPhaseRank || model.progressPhase == "complete" { return "완료" }
        if rank == progressPhaseRank { return model.progressLabel }
        return "대기 중"
    }

    private var readerScreen: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Button {
                    stage = .importDocument
                } label: {
                    Image(systemName: "sidebar.left")
                        .frame(width: 18, height: 18)
                }
                .buttonStyle(WorkspaceIconButtonStyle())

                Picker("보기", selection: .constant(0)) {
                    Text("번역본").tag(0)
                    Text("원문 비교").tag(1)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 168)

                Spacer()

                Text(readerPaperID)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(WorkspacePalette.secondaryText)
                    .lineLimit(1)

                Button("HTML") { model.openOutput(kind: .korean) }
                    .buttonStyle(WorkspaceSecondaryButtonStyle())
                Button("한영 비교") { model.openOutput(kind: .bilingual) }
                    .buttonStyle(WorkspacePrimaryButtonStyle(compact: true))
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
            .padding(.top, 28)
            .background(.thinMaterial)

            Divider()

            if let url = readerURL {
                HStack(spacing: 0) {
                    ReaderOutline(headings: loadedReaderHeadings, selectedAnchor: $readerScrollTarget)
                    Divider()
                    PaperWebPreview(url: url, scrollTarget: readerScrollTarget)
                        .accessibilityHidden(readerPreviewEnabled)
                }
                .onAppear {
                    if loadedReaderHeadings.isEmpty {
                        loadedReaderHeadings = ReaderHeadingParser.parse(url: url)
                    }
                }
            } else {
                EmptyDocumentView(
                    title: "미리볼 결과가 없습니다",
                    subtitle: "새 번역을 시작하면 이곳에서 결과를 읽을 수 있습니다."
                )
            }
        }
    }

    private var recentScreen: some View {
        VStack(alignment: .leading, spacing: 24) {
            screenHeader(step: nil, title: "최근 문서", subtitle: "최근에 번역한 논문을 다시 엽니다.")
            if model.lastPaperID.isEmpty {
                EmptyDocumentView(title: "아직 번역한 문서가 없습니다", subtitle: "새 번역에서 첫 문서를 추가해 보세요.")
            } else {
                Button {
                    destination = .translation
                    stage = .reader
                } label: {
                    HStack(spacing: 14) {
                        Image(systemName: "doc.text")
                            .font(.system(size: 22))
                            .foregroundStyle(WorkspacePalette.blue)
                            .frame(width: 44, height: 52)
                            .background(WorkspacePalette.blue.opacity(0.08))
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.lastPaperID)
                                .font(.system(size: 14, weight: .semibold))
                            Text("한국어 번역 · HTML")
                                .font(.system(size: 12))
                                .foregroundStyle(WorkspacePalette.secondaryText)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .foregroundStyle(WorkspacePalette.tertiaryText)
                    }
                    .padding(14)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .background(WorkspacePalette.panel)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(WorkspacePalette.border))
            }
            Spacer()
        }
        .padding(.horizontal, 42)
        .padding(.vertical, 34)
    }

    private var documentsScreen: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top, spacing: 16) {
                screenHeader(
                    step: nil,
                    title: "내 문서",
                    subtitle: "번역된 문서를 앱 안에서 찾아보고 읽을 수 있습니다."
                )
                Spacer()
                HStack(spacing: 7) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(WorkspacePalette.tertiaryText)
                    TextField("문서 검색", text: $documentSearchText)
                        .textFieldStyle(.plain)
                        .font(.system(size: 12))
                }
                .padding(.horizontal, 10)
                .frame(width: 190, height: 34)
                .background(WorkspacePalette.controlFill)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
                Button {
                    reloadOutputDocuments()
                } label: {
                    Label("새로고침", systemImage: "arrow.clockwise")
                }
                .buttonStyle(WorkspaceSecondaryButtonStyle())
                .help("문서 목록 새로고침")
            }

            if let documentLoadError {
                VStack(spacing: 14) {
                    EmptyDocumentView(
                        title: "문서 폴더를 읽을 수 없습니다",
                        subtitle: documentLoadError
                    )
                    Button("다시 시도") { reloadOutputDocuments() }
                        .buttonStyle(WorkspacePrimaryButtonStyle(compact: true))
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if outputDocuments.isEmpty {
                EmptyDocumentView(
                    title: "저장된 문서가 없습니다",
                    subtitle: "번역이 완료되면 이곳에 문서가 표시됩니다."
                )
            } else if filteredOutputDocuments.isEmpty {
                EmptyDocumentView(
                    title: "검색 결과가 없습니다",
                    subtitle: "다른 문서 이름이나 형식을 검색해 보세요."
                )
            } else {
                VStack(spacing: 0) {
                    HStack {
                        Text("문서 \(filteredOutputDocuments.count)개")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(WorkspacePalette.secondaryText)
                        Spacer()
                        Text("최근 수정 순")
                            .font(.system(size: 11))
                            .foregroundStyle(WorkspacePalette.tertiaryText)
                    }
                    .padding(.horizontal, 16)
                    .frame(height: 38)

                    Divider()

                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(filteredOutputDocuments) { document in
                                Button {
                                    openOutputDocument(document)
                                } label: {
                                    OutputDocumentRow(document: document)
                                }
                                .buttonStyle(.plain)
                                .accessibilityHint("앱 내 리더에서 엽니다")

                                if document.id != filteredOutputDocuments.last?.id {
                                    Divider().padding(.leading, 70)
                                }
                            }
                        }
                    }
                }
                .background(WorkspacePalette.panel)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(WorkspacePalette.border))
            }
        }
        .padding(.horizontal, 42)
        .padding(.vertical, 34)
        .onAppear(perform: reloadOutputDocuments)
    }

    private var filteredOutputDocuments: [OutputDocument] {
        let query = documentSearchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return outputDocuments }
        return outputDocuments.filter {
            $0.fileName.localizedCaseInsensitiveContains(query)
                || $0.paperID.localizedCaseInsensitiveContains(query)
                || $0.formatLabel.localizedCaseInsensitiveContains(query)
        }
    }

    private func reloadOutputDocuments() {
        do {
            outputDocuments = try model.loadOutputDocuments()
            documentLoadError = nil
        } catch {
            outputDocuments = []
            documentLoadError = error.localizedDescription
        }
    }

    private func openOutputDocument(_ document: OutputDocument) {
        model.selectOutputDocument(document)
        loadedReaderHeadings = []
        readerScrollTarget = nil
        destination = .translation
        stage = .reader
    }

    private var settingsScreen: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                screenHeader(step: nil, title: "설정", subtitle: "번역 엔진과 프로젝트 경로를 관리합니다.")

                VStack(spacing: 0) {
                    SettingRow(title: "프로젝트") {
                        TextField("프로젝트 경로", text: $model.repoPath)
                            .textFieldStyle(WorkspaceTextFieldStyle())
                    }
                    Divider().padding(.leading, 18)
                    SettingRow(title: "인증 방식") {
                        Picker("인증 방식", selection: $model.selectedProvider) {
                            ForEach(TranslationProvider.allCases) { provider in
                                Text(provider.displayName).tag(provider)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider().padding(.leading, 18)
                    if model.selectedProvider == .codex {
                        SettingRow(title: "Codex 계정") {
                            HStack(spacing: 10) {
                                Circle()
                                    .fill(model.isCodexAuthenticated ? WorkspacePalette.success : WorkspacePalette.tertiaryText)
                                    .frame(width: 8, height: 8)
                                Text(model.codexAuthStatus)
                                    .foregroundStyle(WorkspacePalette.secondaryText)
                                Spacer()
                                Button("상태 확인") { model.refreshCodexStatus() }
                                    .buttonStyle(WorkspaceSecondaryButtonStyle())
                                Button("ChatGPT로 로그인") { model.startCodexLogin() }
                                    .buttonStyle(WorkspacePrimaryButtonStyle(compact: true))
                            }
                        }
                        Divider().padding(.leading, 18)
                    } else {
                        SettingRow(title: "OpenAI Base URL") {
                            TextField("비워두면 .env의 OPENAI_BASE_URL 사용", text: $model.baseURLOverride)
                                .textFieldStyle(WorkspaceTextFieldStyle())
                        }
                        Divider().padding(.leading, 18)
                    }
                    SettingRow(title: "모델") {
                        Picker("모델", selection: $model.selectedModel) {
                            ForEach(TranslatorModel.modelOptions, id: \.id) { option in
                                Text(option.displayName).tag(option.id)
                            }
                        }
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider().padding(.leading, 18)
                    SettingRow(title: "PDF 레이아웃") {
                        VStack(alignment: .leading, spacing: 5) {
                            Toggle("표·차트·그림을 본문 위치에 삽입", isOn: $model.useAdvancedPDFLayout)
                            Text("디지털 PDF는 원문 추출 · 필요한 페이지만 Unlimited-OCR")
                                .font(.system(size: 11))
                                .foregroundStyle(WorkspacePalette.secondaryText)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if model.selectedProvider == .api {
                        Divider().padding(.leading, 18)
                        SettingRow(title: "API 키") {
                            SecureField("앱에는 저장되지 않습니다", text: $model.apiKeyOverride)
                                .textFieldStyle(WorkspaceTextFieldStyle())
                        }
                    }
                }
                .background(WorkspacePalette.panel)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(WorkspacePalette.border))

                HStack {
                    Button("연결 확인") { model.checkConnection() }
                        .buttonStyle(WorkspaceSecondaryButtonStyle())
                        .disabled(model.isRunning)
                    Spacer()
                    Button("설정 저장") { model.saveSettings() }
                        .buttonStyle(WorkspacePrimaryButtonStyle(compact: true))
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("진행 로그")
                        .font(.system(size: 13, weight: .semibold))
                    ScrollView {
                        Text(model.logText.isEmpty ? "대기 중입니다." : model.logText)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(WorkspacePalette.secondaryText)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                    .frame(minHeight: 120)
                    .padding(12)
                    .background(WorkspacePalette.controlFill)
                    .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                }
            }
            .padding(.horizontal, 42)
            .padding(.vertical, 34)
        }
    }

    private func screenHeader(step: String?, title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 9) {
                Text(step.map { "\($0). \(title)" } ?? title)
                    .font(.system(size: 19, weight: .bold))
                    .tracking(-0.2)
            }
            Text(subtitle)
                .font(.system(size: 13))
                .foregroundStyle(WorkspacePalette.secondaryText)
        }
    }

    private var readerPaperID: String {
        readerPreviewEnabled ? "mmdocrag" : model.lastPaperID
    }

    private var readerURL: URL? {
        if readerPreviewEnabled {
            let url = URL(fileURLWithPath: model.repoPath)
                .appendingPathComponent("outputs/mmdocrag.ko.paper.html")
            return FileManager.default.fileExists(atPath: url.path) ? url : nil
        }
        return model.readerOutputURL()
    }

}

private enum TimelineState {
    case complete
    case current
    case pending
}

private struct ImportModeSelector: View {
    @Binding var selection: ImportMode

    var body: some View {
        HStack(spacing: 0) {
            ForEach(ImportMode.allCases) { mode in
                Button {
                    selection = mode
                } label: {
                    Text(mode.rawValue)
                        .font(.system(size: 12, weight: selection == mode ? .semibold : .medium))
                        .foregroundStyle(selection == mode ? WorkspacePalette.blue : Color.primary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 32)
                        .background(selection == mode ? WorkspacePalette.blue.opacity(0.06) : Color.clear)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .overlay {
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .strokeBorder(selection == mode ? WorkspacePalette.blue : Color.clear, lineWidth: 1.5)
                }
            }
        }
        .padding(2)
        .background(WorkspacePalette.controlFill)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("가져오기 방식")
    }
}

private struct OutputDocumentRow: View {
    let document: OutputDocument

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(WorkspacePalette.blue.opacity(0.08))
                Image(systemName: document.isBilingual ? "rectangle.split.2x1" : "doc.richtext")
                    .font(.system(size: 19, weight: .medium))
                    .foregroundStyle(WorkspacePalette.blue)
            }
            .frame(width: 42, height: 48)

            VStack(alignment: .leading, spacing: 5) {
                Text(document.paperID)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.primary)
                    .lineLimit(1)
                    .truncationMode(.middle)

                HStack(spacing: 7) {
                    Text(document.formatLabel)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(WorkspacePalette.blue)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(WorkspacePalette.blue.opacity(0.08))
                        .clipShape(Capsule())
                    Text(document.modifiedAt, format: .dateTime.year().month().day())
                    Text(document.byteCountLabel)
                }
                .font(.system(size: 11))
                .foregroundStyle(WorkspacePalette.secondaryText)
            }

            Spacer(minLength: 12)

            Image(systemName: "chevron.right")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(WorkspacePalette.tertiaryText)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }
}

private struct SidebarButton: View {
    let title: String
    let icon: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: icon)
                    .frame(width: 18)
                Text(title)
                Spacer()
            }
            .font(.system(size: 13, weight: isSelected ? .semibold : .medium))
            .foregroundStyle(isSelected ? WorkspacePalette.blue : WorkspacePalette.secondaryText)
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(isSelected ? WorkspacePalette.blue.opacity(0.10) : Color.clear)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
    }
}

private struct LanguageMenu: View {
    let title: String
    let value: String
    let icon: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundStyle(WorkspacePalette.blue)
                Text(value)
                Spacer()
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(WorkspacePalette.tertiaryText)
            }
            .font(.system(size: 13, weight: .medium))
            .padding(.horizontal, 12)
            .frame(height: 38)
            .background(WorkspacePalette.controlFill)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
        }
        .frame(maxWidth: .infinity)
    }
}

private struct DividerLabel: View {
    let text: String

    var body: some View {
        HStack(spacing: 12) {
            Rectangle().fill(WorkspacePalette.border).frame(height: 1)
            Text(text)
                .font(.system(size: 11))
                .foregroundStyle(WorkspacePalette.tertiaryText)
            Rectangle().fill(WorkspacePalette.border).frame(height: 1)
        }
    }
}

private struct PreservationCard: View {
    let title: String
    let icon: String
    let state: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 16, weight: .medium))
            Text(title)
                .font(.system(size: 12, weight: .semibold))
            Label(state, systemImage: "checkmark.circle.fill")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(WorkspacePalette.success)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(WorkspacePalette.panel)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(WorkspacePalette.border))
    }
}

private struct TimelineRow: View {
    let title: String
    let detail: String
    let state: TimelineState
    var showsLine = true

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .fill(circleColor)
                        .frame(width: 20, height: 20)
                    Image(systemName: state == .complete ? "checkmark" : state == .current ? "circle.fill" : "")
                        .font(.system(size: state == .current ? 7 : 10, weight: .bold))
                        .foregroundStyle(.white)
                }
                if showsLine {
                    Rectangle()
                        .fill(WorkspacePalette.border)
                        .frame(width: 1, height: 34)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: 13, weight: .semibold))
                Text(detail)
                    .font(.system(size: 11))
                    .foregroundStyle(WorkspacePalette.secondaryText)
            }
            .padding(.top, 1)
            Spacer()
        }
    }

    private var circleColor: Color {
        switch state {
        case .complete: return WorkspacePalette.success
        case .current: return WorkspacePalette.blue
        case .pending: return WorkspacePalette.border
        }
    }
}

private struct SettingRow<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        HStack(spacing: 18) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 118, alignment: .leading)
            content
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
    }
}

private struct EmptyDocumentView: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(WorkspacePalette.tertiaryText)
            Text(title)
                .font(.system(size: 15, weight: .semibold))
            Text(subtitle)
                .font(.system(size: 12))
                .foregroundStyle(WorkspacePalette.secondaryText)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct ReaderHeading: Identifiable {
    let id: String
    let title: String
    let level: Int
}

private enum ReaderHeadingParser {
    static func parse(url: URL) -> [ReaderHeading] {
        guard let html = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        var headings: [ReaderHeading] = []
        var sectionID: String?
        var headingLevel: Int?
        var headingHTML = ""

        for rawLine in html.split(whereSeparator: \.isNewline) {
            let line = String(rawLine)
            if line.contains("<section"), let id = attribute(named: "id", in: line) {
                sectionID = id
            }

            if headingLevel == nil {
                if line.contains("<h2") {
                    headingLevel = 2
                    headingHTML = line
                } else if line.contains("<h3") {
                    headingLevel = 3
                    headingHTML = line
                }
            } else {
                headingHTML += " " + line
            }

            guard let level = headingLevel, headingHTML.contains("</h\(level)>") else { continue }
            let title = headingHTML
                .replacingOccurrences(of: #"<[^>]+>"#, with: "", options: .regularExpression)
                .replacingOccurrences(of: "&amp;", with: "&")
                .replacingOccurrences(of: "&nbsp;", with: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)

            if let sectionID, !title.isEmpty {
                headings.append(ReaderHeading(id: sectionID, title: title, level: level))
            }
            headingLevel = nil
            headingHTML = ""
            sectionID = nil
            if headings.count == 18 { break }
        }
        return headings
    }

    private static func attribute(named name: String, in text: String) -> String? {
        let marker = "\(name)=\""
        guard let start = text.range(of: marker) else { return nil }
        let suffix = text[start.upperBound...]
        guard let end = suffix.firstIndex(of: "\"") else { return nil }
        return String(suffix[..<end])
    }
}

private struct ReaderOutline: View {
    let headings: [ReaderHeading]
    @Binding var selectedAnchor: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("목차")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 13)

            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(headings) { heading in
                        Button {
                            selectedAnchor = heading.id
                        } label: {
                            Text(heading.title)
                                .font(.system(size: 11, weight: selectedAnchor == heading.id ? .semibold : .regular))
                                .foregroundStyle(selectedAnchor == heading.id ? WorkspacePalette.blue : WorkspacePalette.secondaryText)
                                .lineLimit(2)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.leading, heading.level == 3 ? 12 : 0)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 7)
                                .background(selectedAnchor == heading.id ? WorkspacePalette.blue.opacity(0.10) : Color.clear)
                                .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 6)
            }
        }
        .frame(width: 170)
        .background(WorkspacePalette.panel.opacity(0.72))
    }
}

private struct PaperWebPreview: NSViewRepresentable {
    let url: URL
    let scrollTarget: String?

    final class Coordinator {
        var lastScrollTarget: String?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.setValue(false, forKey: "drawsBackground")
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        if view.url != url {
            // Generated readers live in outputs/ while PDF page and layout crops
            // live in the sibling inputs/assets/ tree. WKWebView blocks those
            // images unless the repository root is included in its read scope.
            let repositoryRoot = url.deletingLastPathComponent().deletingLastPathComponent()
            view.loadFileURL(url, allowingReadAccessTo: repositoryRoot)
        }
        guard let scrollTarget, context.coordinator.lastScrollTarget != scrollTarget else { return }
        context.coordinator.lastScrollTarget = scrollTarget
        let escaped = scrollTarget.replacingOccurrences(of: "'", with: "\\'")
        view.evaluateJavaScript("document.getElementById('\(escaped)')?.scrollIntoView({behavior:'smooth', block:'start'});")
    }
}

private enum WorkspacePalette {
    static let canvas = Color(nsColor: .textBackgroundColor)
    static let sidebar = Color(nsColor: .windowBackgroundColor)
    static let panel = Color(nsColor: .controlBackgroundColor)
    static let controlFill = Color(nsColor: .unemphasizedSelectedContentBackgroundColor).opacity(0.72)
    static let border = Color(nsColor: .separatorColor).opacity(0.58)
    static let secondaryText = Color.secondary
    static let tertiaryText = Color.secondary.opacity(0.62)
    static let blue = Color(nsColor: .labelColor)
    static let success = Color(red: 0.25, green: 0.64, blue: 0.31)
}

private struct WorkspaceTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)
            .font(.system(size: 13))
            .padding(.horizontal, 11)
            .frame(height: 38)
            .background(WorkspacePalette.controlFill)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
    }
}

private struct WorkspacePrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var colorScheme
    var compact = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(colorScheme == .dark ? Color.black : Color.white)
            .padding(.horizontal, compact ? 14 : 18)
            .frame(height: compact ? 34 : 44)
            .background(
                LinearGradient(
                    colors: [
                        WorkspacePalette.blue.opacity(isEnabled ? (configuration.isPressed ? 0.76 : 0.94) : 0.34),
                        WorkspacePalette.blue.opacity(isEnabled ? (configuration.isPressed ? 0.68 : 0.82) : 0.28)
                    ],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: compact ? 8 : 9, style: .continuous))
            .shadow(color: WorkspacePalette.blue.opacity(isEnabled ? 0.18 : 0), radius: 10, y: 4)
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .animation(reduceMotion ? .linear(duration: 0.08) : .spring(response: 0.24, dampingFraction: 0.9), value: configuration.isPressed)
    }
}

private struct WorkspaceSecondaryButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(Color.primary)
            .padding(.horizontal, 14)
            .frame(height: 34)
            .background(WorkspacePalette.controlFill.opacity(configuration.isPressed ? 0.65 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .animation(reduceMotion ? .linear(duration: 0.08) : .spring(response: 0.24, dampingFraction: 0.9), value: configuration.isPressed)
    }
}

private struct WorkspaceIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(WorkspacePalette.secondaryText)
            .frame(width: 38, height: 38)
            .background(WorkspacePalette.controlFill.opacity(configuration.isPressed ? 0.62 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(WorkspacePalette.border))
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: TranslatorModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    @State private var isDropTargeted = false

    var body: some View {
        ZStack {
            glassBackground
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    HStack(alignment: .top, spacing: 18) {
                        VStack(spacing: 18) {
                            dropZone
                            clipboardPanel
                        }
                        .frame(maxWidth: .infinity)

                        VStack(spacing: 18) {
                            outputPanel
                            runtimePanel
                        }
                        .frame(width: 300)
                    }
                    settingsPanel
                    logPanel
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 24)
            }
        }
    }

    private var glassBackground: some View {
        ZStack {
            LinearGradient(
                colors: colorScheme == .dark
                    ? [Color(red: 0.08, green: 0.09, blue: 0.11), Color(red: 0.07, green: 0.11, blue: 0.12)]
                    : [Color(red: 0.97, green: 0.98, blue: 1.00), Color(red: 0.93, green: 0.97, blue: 0.98)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            if !reduceTransparency {
                Circle()
                    .fill(AppPalette.accentTeal.opacity(colorScheme == .dark ? 0.12 : 0.16))
                    .frame(width: 440, height: 440)
                    .blur(radius: 90)
                    .offset(x: -360, y: -280)
                Circle()
                    .fill(AppPalette.accentBlue.opacity(colorScheme == .dark ? 0.10 : 0.11))
                    .frame(width: 380, height: 380)
                    .blur(radius: 100)
                    .offset(x: 380, y: 300)
            }
        }
        .ignoresSafeArea()
    }

    private var header: some View {
        HStack(spacing: 14) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 27, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 50, height: 50)
                .background(AppPalette.accentGradient, in: RoundedRectangle(cornerRadius: 13, style: .continuous))
                .shadow(color: AppPalette.accentTeal.opacity(0.24), radius: 12, y: 5)
            VStack(alignment: .leading, spacing: 3) {
                Text("KPaper")
                    .font(AppTypography.display(size: 26, weight: .bold))
                    .tracking(-0.5)
                    .foregroundStyle(AppPalette.textPrimary)
                Text("논문의 구조를 지키며 읽기 좋은 한국어 문서로 바꿉니다.")
                    .font(AppTypography.korean(size: 14, weight: .medium))
                    .foregroundStyle(AppPalette.textSecondary)
            }
            Spacer()
            if model.isRunning {
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text(model.progressLabel)
                            .font(AppTypography.korean(size: 12, weight: .medium))
                            .foregroundStyle(AppPalette.textSecondary)
                        Spacer()
                        if model.progressTotal > 0 {
                            Text("\(Int(model.progressFraction * 100))%")
                                .font(AppTypography.korean(size: 12, weight: .semibold))
                                .foregroundStyle(AppPalette.accentTeal)
                        }
                    }
                    if model.progressTotal > 0 {
                        ProgressView(value: model.progressFraction)
                            .tint(AppPalette.accentTeal)
                    } else {
                        ProgressView()
                    }
                }
                .frame(width: 300)
                Button {
                    model.cancel()
                } label: {
                    Label("중지", systemImage: "stop.fill")
                }
                .buttonStyle(GlassButtonStyle(kind: .secondary))
            } else {
                statusBadge
            }
        }
        .padding(.horizontal, 2)
        .padding(.vertical, 4)
    }

    private var statusBadge: some View {
        HStack(spacing: 7) {
            Image(systemName: model.statusText == "완료" ? "checkmark.circle.fill" : "circle.fill")
                .font(.system(size: model.statusText == "완료" ? 13 : 7, weight: .semibold))
                .foregroundStyle(model.statusText == "완료" ? AppPalette.accentTeal : AppPalette.textTertiary)
            Text(model.statusText)
                .font(AppTypography.korean(size: 12, weight: .semibold))
        }
        .foregroundStyle(AppPalette.textSecondary)
        .padding(.horizontal, 11)
        .padding(.vertical, 7)
        .background(.thinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(AppPalette.stroke, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("현재 상태: \(model.statusText)")
    }

    private var dropZone: some View {
        VStack(spacing: 12) {
            Image(systemName: isDropTargeted ? "arrow.down.doc.fill" : "arrow.down.doc")
                .font(.system(size: 32, weight: .semibold))
                .foregroundStyle(isDropTargeted ? .white : AppPalette.accentTeal)
                .frame(width: 58, height: 58)
                .background(isDropTargeted ? AppPalette.accentTeal : AppPalette.accentTeal.opacity(0.10), in: Circle())
                .scaleEffect(isDropTargeted ? 1.06 : 1)
            Text(isDropTargeted ? "놓아서 번역 시작" : "PDF를 여기에 놓으세요")
                .font(AppTypography.korean(size: 19, weight: .semibold))
                .foregroundStyle(AppPalette.textPrimary)
            Text("가져오기부터 번역, 문서 스타일 적용까지 자동으로 진행됩니다.")
                .font(AppTypography.korean(size: 14, weight: .medium))
                .foregroundStyle(AppPalette.textSecondary)
        }
        .frame(maxWidth: .infinity, minHeight: 184)
        .background(isDropTargeted ? AppPalette.accentTeal.opacity(0.15) : AppPalette.glass, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(isDropTargeted ? AppPalette.accentTeal.opacity(0.70) : AppPalette.stroke, style: StrokeStyle(lineWidth: isDropTargeted ? 2 : 1.2, dash: [9, 6])))
        .shadow(color: isDropTargeted ? AppPalette.accentTeal.opacity(0.18) : AppPalette.shadow, radius: isDropTargeted ? 24 : 18, y: 8)
        .scaleEffect(isDropTargeted ? 1.008 : 1)
        .animation(reduceMotion ? .linear(duration: 0.12) : .spring(response: 0.32, dampingFraction: 0.82), value: isDropTargeted)
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $isDropTargeted) { providers in
            model.handleDrop(providers: providers)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("PDF 번역 영역")
        .accessibilityHint("PDF 파일을 끌어다 놓으면 번역을 시작합니다")
    }

    private var clipboardPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("웹 논문 번역", systemImage: "link")
            VStack(alignment: .leading, spacing: 10) {
                Text("클립보드의 arXiv 또는 ar5iv 주소를 읽어 바로 번역합니다.")
                    .font(AppTypography.korean(size: 14, weight: .medium))
                    .foregroundStyle(AppPalette.textSecondary)
                HStack {
                    Button {
                        model.translateClipboardURL()
                    } label: {
                        Label("붙여넣어 번역", systemImage: "doc.on.clipboard")
                    }
                    .buttonStyle(GlassButtonStyle(kind: .primary))
                    .fixedSize()
                    .disabled(model.isRunning)

                    Button {
                        model.loadClipboardPreview()
                    } label: {
                        Label("주소 확인", systemImage: "eye")
                    }
                    .buttonStyle(GlassButtonStyle(kind: .secondary))
                    .fixedSize()
                    .disabled(model.isRunning)

                    Text(model.clipboardPreview)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .foregroundStyle(AppPalette.textSecondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private var settingsPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("설정", systemImage: "slider.horizontal.3")
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                GridRow {
                    Text("프로젝트")
                    TextField("KPaper 경로", text: $model.repoPath)
                        .textFieldStyle(GlassTextFieldStyle())
                    Button("저장") {
                        model.saveSettings()
                    }
                    .buttonStyle(GlassButtonStyle(kind: .secondary))
                    .fixedSize()
                }
                GridRow {
                    Text("OpenAI Base URL")
                    TextField("비워두면 .env의 OPENAI_BASE_URL 사용", text: $model.baseURLOverride)
                        .textFieldStyle(GlassTextFieldStyle())
                    Button("연결 확인") {
                        model.runDoctor()
                    }
                    .buttonStyle(GlassButtonStyle(kind: .secondary))
                    .fixedSize()
                    .disabled(model.isRunning)
                }
                GridRow {
                    Text("모델")
                    Picker("Model", selection: $model.selectedModel) {
                        ForEach(TranslatorModel.modelOptions, id: \.id) { option in
                            Text(option.displayName).tag(option.id)
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .onChange(of: model.selectedModel) { _ in
                        model.saveSettings()
                    }
                    Text("번역 요청에 사용합니다.")
                        .font(AppTypography.korean(size: 12, weight: .regular))
                        .foregroundStyle(AppPalette.textTertiary)
                }
                GridRow {
                    Text("API 키")
                    SecureField(".env를 쓰려면 비워두세요", text: $model.apiKeyOverride)
                        .textFieldStyle(GlassTextFieldStyle())
                    Text("앱에는 저장하지 않습니다.")
                        .font(AppTypography.korean(size: 12, weight: .regular))
                        .foregroundStyle(AppPalette.textTertiary)
                }
            }
            .foregroundStyle(AppPalette.textPrimary)
        }
        .glassCard()
    }

    private var outputPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            sectionTitle("번역 결과", systemImage: "checkmark.seal")
            VStack(alignment: .leading, spacing: 4) {
                Text(model.statusText)
                    .font(AppTypography.korean(size: 18, weight: .semibold))
                    .foregroundStyle(AppPalette.textPrimary)
                Text(model.lastPaperID.isEmpty ? "아직 생성된 결과가 없습니다." : model.lastPaperID)
                    .font(AppTypography.korean(size: 12, weight: .regular))
                    .foregroundStyle(AppPalette.textSecondary)
            }
            VStack(spacing: 8) {
                Button {
                    model.openOutput(kind: .korean)
                } label: {
                    Label("한국어 열기", systemImage: "doc.text")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(GlassButtonStyle(kind: .primary))
                .disabled(model.lastPaperID.isEmpty)
                Button {
                    model.openOutput(kind: .bilingual)
                } label: {
                    Label("한영 병행 열기", systemImage: "rectangle.split.2x1")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(GlassButtonStyle(kind: .secondary))
                .disabled(model.lastPaperID.isEmpty)
                Button {
                    model.openOutputsFolder()
                } label: {
                    Label("결과 폴더 열기", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(GlassButtonStyle(kind: .secondary))
            }
        }
        .glassCard()
    }

    private var logPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("진행 로그", systemImage: "terminal")
            ScrollViewReader { proxy in
                ScrollView {
                    Text(model.logText.isEmpty ? "대기 중입니다." : model.logText)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(model.logText.isEmpty ? AppPalette.textTertiary : AppPalette.textPrimary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .id("log-end")
                }
                .frame(minHeight: 190)
                .onChange(of: model.logText) { _ in
                    proxy.scrollTo("log-end", anchor: .bottom)
                }
            }
        }
        .glassCard()
    }

    private var runtimePanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("실행 환경", systemImage: "cpu")
            Label("macOS 네이티브 앱", systemImage: "checkmark.circle.fill")
            Label("uv로 관리되는 Python", systemImage: "checkmark.circle.fill")
            Label("CLI와 동일한 번역 경로", systemImage: "checkmark.circle.fill")
            Text("처음 실행하기 전 프로젝트에서 `uv sync`로 의존성을 준비하세요.")
                .font(AppTypography.korean(size: 12, weight: .regular))
                .foregroundStyle(AppPalette.textSecondary)
        }
        .font(AppTypography.display(size: 12, weight: .medium))
        .foregroundStyle(AppPalette.textSecondary)
        .glassCard()
    }

    private func sectionTitle(_ text: String, systemImage: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .foregroundStyle(AppPalette.accentTeal)
            Text(text)
                .font(AppTypography.korean(size: 16, weight: .semibold))
                .foregroundStyle(AppPalette.textPrimary)
        }
    }
}

private enum AppPalette {
    static let textPrimary = Color.primary
    static let textSecondary = Color.secondary
    static let textTertiary = Color.secondary.opacity(0.72)
    static let glass = Color(nsColor: .controlBackgroundColor).opacity(0.58)
    static let stroke = Color(nsColor: .separatorColor).opacity(0.55)
    static let shadow = Color.black.opacity(0.12)
    static let accentTeal = Color(red: 0.12, green: 0.58, blue: 0.54)
    static let accentBlue = Color(red: 0.12, green: 0.44, blue: 0.78)
    static let accentGradient = LinearGradient(
        colors: [accentTeal, accentBlue],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

private enum AppTypography {
    static func display(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .default)
    }

    static func korean(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .custom("Apple SD Gothic Neo", size: size).weight(weight)
    }
}

private func glassStroke(cornerRadius: CGFloat = 18) -> some View {
    RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        .strokeBorder(AppPalette.stroke, lineWidth: 1)
}

private struct GlassCardModifier: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        content
            .padding(18)
            .background(reduceTransparency ? AnyShapeStyle(Color(nsColor: .windowBackgroundColor)) : AnyShapeStyle(.ultraThinMaterial), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .background(AppPalette.glass, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(glassStroke(cornerRadius: 18))
            .shadow(color: AppPalette.shadow, radius: 18, x: 0, y: 10)
            .overlay(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [Color.white.opacity(0.70), Color.white.opacity(0.12)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(height: 1)
                    .padding(.horizontal, 12)
            }
    }
}

private extension View {
    func glassCard() -> some View {
        modifier(GlassCardModifier())
    }
}

private struct GlassTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .textFieldStyle(.plain)
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .font(AppTypography.korean(size: 14, weight: .medium))
            .foregroundStyle(AppPalette.textPrimary)
            .background(Color(nsColor: .textBackgroundColor).opacity(0.78), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(AppPalette.stroke, lineWidth: 1))
    }
}

private enum GlassButtonKind {
    case primary
    case secondary
}

private struct GlassButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let kind: GlassButtonKind

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AppTypography.korean(size: 13, weight: .semibold))
            .foregroundStyle(kind == .primary ? .white : AppPalette.textPrimary)
            .lineLimit(1)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(background(isPressed: configuration.isPressed), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(kind == .primary ? Color.white.opacity(0.24) : AppPalette.stroke, lineWidth: 1))
            .shadow(color: AppPalette.shadow.opacity(configuration.isPressed ? 0.45 : 1.0), radius: configuration.isPressed ? 4 : 12, y: configuration.isPressed ? 2 : 7)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(isEnabled ? 1 : 0.46)
            .animation(reduceMotion ? .linear(duration: 0.08) : .spring(response: 0.24, dampingFraction: 0.88), value: configuration.isPressed)
    }

    private func background(isPressed: Bool) -> LinearGradient {
        let opacity = isPressed ? 0.76 : 0.90
        switch kind {
        case .primary:
            return LinearGradient(colors: [AppPalette.accentTeal.opacity(opacity), AppPalette.accentBlue.opacity(opacity)], startPoint: .topLeading, endPoint: .bottomTrailing)
        case .secondary:
            return LinearGradient(
                colors: [
                    Color(nsColor: .controlBackgroundColor).opacity(isPressed ? 0.70 : 0.92),
                    Color(nsColor: .controlBackgroundColor).opacity(isPressed ? 0.52 : 0.74)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }
}

enum OutputKind {
    case korean
    case bilingual
}

struct OutputDocument: Identifiable {
    let url: URL
    let paperID: String
    let modifiedAt: Date
    let byteCount: Int64
    let isBilingual: Bool

    var id: String { url.path }
    var fileName: String { url.lastPathComponent }
    var formatLabel: String { isBilingual ? "한영 비교" : "한국어" }
    var byteCountLabel: String { ByteCountFormatter.string(fromByteCount: byteCount, countStyle: .file) }
}

enum TranslationProvider: String, CaseIterable, Identifiable {
    case codex
    case api

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .codex: return "ChatGPT / Codex 구독"
        case .api: return "OpenAI 호환 API"
        }
    }
}

struct RuntimeSettings {
    let repoPath: String
    let baseURLOverride: String
    let apiKeyOverride: String
    let model: String
    let provider: TranslationProvider
    let useAdvancedPDFLayout: Bool
}

struct ModelOption {
    let displayName: String
    let id: String
}

final class TranslatorModel: ObservableObject {
    static let defaultModel = "gpt-5.4-mini"
    static let modelOptions = [
        ModelOption(displayName: "GPT-5.6 Sol", id: "gpt-5.6"),
        ModelOption(displayName: "GPT-5.6 Terra", id: "gpt-5.6-terra"),
        ModelOption(displayName: "GPT-5.6 Luna", id: "gpt-5.6-luna"),
        ModelOption(displayName: "GPT-5.5", id: "gpt-5.5"),
        ModelOption(displayName: "GPT-5.4", id: "gpt-5.4"),
        ModelOption(displayName: "GPT-5.4 Mini", id: "gpt-5.4-mini"),
        ModelOption(displayName: "GPT-5.3 Codex", id: "gpt-5.3-codex")
    ]

    @Published var repoPath: String
    @Published var baseURLOverride: String
    @Published var apiKeyOverride = ""
    @Published var selectedModel: String
    @Published var selectedProvider: TranslationProvider
    @Published var useAdvancedPDFLayout: Bool
    @Published var codexAuthStatus = "상태를 확인해주세요"
    @Published var isCodexAuthenticated = false
    @Published var clipboardPreview = ""
    @Published var logText = ""
    @Published var statusText = "대기"
    @Published var lastPaperID = ""
    @Published var lastKoreanOutput = ""
    @Published var lastBilingualOutput = ""
    @Published var selectedReaderOutput = ""
    @Published var isRunning = false
    @Published var progressCompleted = 0
    @Published var progressTotal = 0
    @Published var progressLabel = "준비 중"
    @Published var progressPhase = "preparing"
    @Published var progressDetail = ""
    @Published var workflowStartedAt = Date()
    @Published private(set) var jobs: [TranslationJob] = []
    @Published private(set) var selectedWorkflowID: UUID?

    var runningJobs: [TranslationJob] { jobs.filter(\.isRunning) }
    var selectedWorkflowIsRunning: Bool {
        guard let selectedWorkflowID else { return false }
        return jobs.first(where: { $0.id == selectedWorkflowID })?.isRunning == true
    }

    var progressFraction: Double {
        guard progressTotal > 0 else { return 0 }
        return min(1, max(0, Double(progressCompleted) / Double(progressTotal)))
    }

    private var currentProcesses: [UUID: Process] = [:]
    private var currentProcess: Process?
    private let defaults = UserDefaults.standard

    init() {
        let detectedRepo = Self.detectRepoPath() ?? FileManager.default.currentDirectoryPath
        let storedRepo = defaults.string(forKey: "repoPath")
        if let storedRepo, Self.isLiteParseRepo(atPath: storedRepo) {
            repoPath = storedRepo
        } else {
            repoPath = detectedRepo
            defaults.set(detectedRepo, forKey: "repoPath")
        }
        baseURLOverride = defaults.string(forKey: "baseURLOverride") ?? ""
        let storedModel = defaults.string(forKey: "selectedModel") ?? Self.defaultModel
        selectedModel = Self.modelOptions.contains { $0.id == storedModel } ? storedModel : Self.defaultModel
        selectedProvider = TranslationProvider(rawValue: defaults.string(forKey: "selectedProvider") ?? "") ?? .api
        useAdvancedPDFLayout = defaults.object(forKey: "useAdvancedPDFLayout") as? Bool ?? true
        if selectedProvider == .codex {
            DispatchQueue.main.async { [weak self] in self?.refreshCodexStatus() }
        }
    }

    func saveSettings() {
        defaults.set(repoPath, forKey: "repoPath")
        defaults.set(baseURLOverride, forKey: "baseURLOverride")
        defaults.set(selectedModel, forKey: "selectedModel")
        defaults.set(selectedProvider.rawValue, forKey: "selectedProvider")
        defaults.set(useAdvancedPDFLayout, forKey: "useAdvancedPDFLayout")
        appendLog("settings saved")
    }

    func loadClipboardPreview() {
        clipboardPreview = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func translateClipboardURL() {
        let value = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        clipboardPreview = value
        translateURL(value)
    }

    func translateURL(_ rawValue: String) {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        clipboardPreview = value
        guard let url = URL(string: value), let scheme = url.scheme, scheme.hasPrefix("http") else {
            appendLog("error: enter a valid HTTP(S) URL")
            return
        }
        let paperID = Self.paperID(from: url)
        let settings = runtimeSettings()
        startWorkflow(paperID: paperID, title: "URL 번역") { [weak self] workflowID in
            try await self?.runCommand(["fetch", "--paper-id", paperID, "--source-url", value, "--force", "--json"], settings: settings, workflowID: workflowID)
            try Self.validateTranslationProvider(settings: settings)
            try await self?.runCommand(["translate", "--paper-id", paperID, "--provider", settings.provider.rawValue, "--model", settings.model, "--json"], settings: settings, workflowID: workflowID)
            try await self?.runCommand(["restyle", "--paper-id", paperID, "--json"], settings: settings, workflowID: workflowID)
        }
    }

    func handleDrop(providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) }) else {
            appendLog("error: dropped item is not a file")
            return false
        }
        provider.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { [weak self] data, error in
            if let error {
                DispatchQueue.main.async {
                    self?.appendLog("error: \(error.localizedDescription)")
                }
                return
            }
            guard
                let data,
                let raw = String(data: data, encoding: .utf8),
                let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines))
            else {
                DispatchQueue.main.async {
                    self?.appendLog("error: could not read dropped file URL")
                }
                return
            }
            DispatchQueue.main.async {
                self?.translatePDF(url)
            }
        }
        return true
    }

    func translatePDF(_ url: URL) {
        guard url.pathExtension.lowercased() == "pdf" else {
            appendLog("error: only PDF files are supported for drag and drop")
            return
        }
        let paperID = Self.sanitizedID(from: url.deletingPathExtension().lastPathComponent)
        let title = url.deletingPathExtension().lastPathComponent
        let settings = runtimeSettings()
        startWorkflow(paperID: paperID, title: "PDF 번역") { [weak self] workflowID in
            var importArguments = ["pdf-import", "--paper-id", paperID, "--pdf", url.path, "--title", title, "--progress", "--json"]
            if settings.useAdvancedPDFLayout {
                importArguments += [
                    "--layout-backend", "auto",
                    "--layout-model", "sahilchachra/unlimited-ocr-mxfp8-mlx"
                ]
            } else {
                importArguments += ["--layout-backend", "liteparse"]
            }
            try await self?.runCommand(importArguments, settings: settings, workflowID: workflowID)
            try Self.validateTranslationProvider(settings: settings)
            try await self?.runCommand(["translate", "--paper-id", paperID, "--provider", settings.provider.rawValue, "--model", settings.model, "--json"], settings: settings, workflowID: workflowID)
            try await self?.runCommand(["restyle", "--paper-id", paperID, "--json"], settings: settings, workflowID: workflowID)
        }
    }

    func runDoctor() {
        let settings = runtimeSettings()
        startWorkflow(paperID: lastPaperID, title: "doctor") { [weak self] workflowID in
            try await self?.runCommand(["doctor", "--json"], settings: settings, workflowID: workflowID)
        }
    }

    func checkConnection() {
        if selectedProvider == .codex {
            refreshCodexStatus()
        } else {
            runDoctor()
        }
    }

    func refreshCodexStatus() {
        guard !isRunning else { return }
        codexAuthStatus = "확인 중…"
        Task {
            do {
                let output = try await runCodexCLI(["login", "status"])
                await MainActor.run {
                    isCodexAuthenticated = output.localizedCaseInsensitiveContains("logged in using ChatGPT")
                    codexAuthStatus = isCodexAuthenticated ? "ChatGPT에 로그인됨" : output.trimmingCharacters(in: .whitespacesAndNewlines)
                    appendLog(output.trimmingCharacters(in: .whitespacesAndNewlines))
                }
            } catch {
                await MainActor.run {
                    isCodexAuthenticated = false
                    codexAuthStatus = "Codex 로그인이 필요합니다"
                    appendLog("error: \(error.localizedDescription)")
                }
            }
        }
    }

    func startCodexLogin() {
        guard !isRunning else { return }
        setRunning(true, status: "Codex 로그인 중")
        codexAuthStatus = "브라우저에서 로그인을 완료해주세요"
        appendLog("Codex managed ChatGPT OAuth login started")
        Task {
            do {
                let output = try await runCodexCLI(["login"])
                await MainActor.run {
                    setRunning(false, status: "대기")
                    appendLog(output.trimmingCharacters(in: .whitespacesAndNewlines))
                    refreshCodexStatus()
                }
            } catch {
                await MainActor.run {
                    setRunning(false, status: "로그인 실패")
                    codexAuthStatus = "Codex 로그인에 실패했습니다"
                    appendLog("error: \(error.localizedDescription)")
                }
            }
        }
    }

    func cancel() {
        guard let workflowID = selectedWorkflowID else { return }
        currentProcesses[workflowID]?.terminate()
        currentProcesses[workflowID] = nil
        updateJob(workflowID) { job in
            job.statusText = "중지됨"
            job.isRunning = false
            Self.append("cancel requested", to: &job.logText)
        }
        syncRunningState(status: "중지됨")
        if let job = jobs.first(where: { $0.id == workflowID }) {
            logText = job.logText
        }
    }

    func openOutput(kind: OutputKind) {
        guard let url = outputURL(kind: kind) else { return }
        NSWorkspace.shared.open(url)
    }

    func outputURL(kind: OutputKind) -> URL? {
        guard !lastPaperID.isEmpty else { return nil }
        let rememberedPath = kind == .korean ? lastKoreanOutput : lastBilingualOutput
        if !rememberedPath.isEmpty {
            return URL(fileURLWithPath: repoPath).appendingPathComponent(rememberedPath)
        }
        let suffix = kind == .korean ? ".ko.paper.html" : ".ko-en.paper.html"
        let url = URL(fileURLWithPath: repoPath)
                .appendingPathComponent("outputs")
                .appendingPathComponent("\(lastPaperID)\(suffix)")
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    func readerOutputURL() -> URL? {
        if !selectedReaderOutput.isEmpty {
            let selectedURL = URL(fileURLWithPath: repoPath).appendingPathComponent(selectedReaderOutput)
            if FileManager.default.fileExists(atPath: selectedURL.path) {
                return selectedURL
            }
        }
        return outputURL(kind: .korean)
    }

    func loadOutputDocuments() throws -> [OutputDocument] {
        let outputFolder = URL(fileURLWithPath: repoPath).appendingPathComponent("outputs", isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: outputFolder.path, isDirectory: &isDirectory) else {
            return []
        }
        guard isDirectory.boolValue else {
            throw AppError.message("outputs 경로가 폴더가 아닙니다: \(outputFolder.path)")
        }

        let keys: Set<URLResourceKey> = [.isRegularFileKey, .contentModificationDateKey, .fileSizeKey]
        return try FileManager.default.contentsOfDirectory(
            at: outputFolder,
            includingPropertiesForKeys: Array(keys),
            options: [.skipsHiddenFiles]
        )
        .compactMap { url -> OutputDocument? in
            let fileName = url.lastPathComponent
            let bilingualSuffix = ".ko-en.paper.html"
            let koreanSuffix = ".ko.paper.html"
            let isBilingual: Bool
            let paperID: String

            if fileName.hasSuffix(bilingualSuffix) {
                isBilingual = true
                paperID = String(fileName.dropLast(bilingualSuffix.count))
            } else if fileName.hasSuffix(koreanSuffix) {
                isBilingual = false
                paperID = String(fileName.dropLast(koreanSuffix.count))
            } else {
                return nil
            }

            guard let values = try? url.resourceValues(forKeys: keys), values.isRegularFile == true else {
                return nil
            }
            return OutputDocument(
                url: url,
                paperID: paperID,
                modifiedAt: values.contentModificationDate ?? .distantPast,
                byteCount: Int64(values.fileSize ?? 0),
                isBilingual: isBilingual
            )
        }
        .sorted {
            if $0.modifiedAt == $1.modifiedAt {
                return $0.fileName.localizedStandardCompare($1.fileName) == .orderedAscending
            }
            return $0.modifiedAt > $1.modifiedAt
        }
    }

    func selectOutputDocument(_ document: OutputDocument) {
        lastPaperID = document.paperID
        selectedReaderOutput = "outputs/\(document.fileName)"

        let outputFolder = URL(fileURLWithPath: repoPath).appendingPathComponent("outputs", isDirectory: true)
        let koreanName = "\(document.paperID).ko.paper.html"
        let bilingualName = "\(document.paperID).ko-en.paper.html"
        let koreanURL = outputFolder.appendingPathComponent(koreanName)
        let bilingualURL = outputFolder.appendingPathComponent(bilingualName)
        lastKoreanOutput = FileManager.default.fileExists(atPath: koreanURL.path) ? "outputs/\(koreanName)" : ""
        lastBilingualOutput = FileManager.default.fileExists(atPath: bilingualURL.path) ? "outputs/\(bilingualName)" : ""
    }

    func openOutputsFolder() {
        let url = URL(fileURLWithPath: repoPath).appendingPathComponent("outputs", isDirectory: true)
        NSWorkspace.shared.open(url)
    }

    private func runtimeSettings() -> RuntimeSettings {
        RuntimeSettings(
            repoPath: repoPath,
            baseURLOverride: baseURLOverride.trimmingCharacters(in: .whitespacesAndNewlines),
            apiKeyOverride: apiKeyOverride.trimmingCharacters(in: .whitespacesAndNewlines),
            model: selectedModel.trimmingCharacters(in: .whitespacesAndNewlines),
            provider: selectedProvider,
            useAdvancedPDFLayout: useAdvancedPDFLayout
        )
    }

    private func startWorkflow(paperID: String, title: String, operation: @escaping (UUID) async throws -> Void) {
        let workflowID = UUID()
        jobs.append(TranslationJob(
            id: workflowID,
            paperID: paperID,
            title: title,
            statusText: "\(title) 실행 중",
            isRunning: true
        ))
        selectedWorkflowID = workflowID
        logText = ""
        lastPaperID = paperID
        lastKoreanOutput = ""
        lastBilingualOutput = ""
        selectedReaderOutput = ""
        progressCompleted = 0
        progressTotal = 0
        progressLabel = "준비 중"
        progressPhase = "preparing"
        progressDetail = ""
        workflowStartedAt = Date()
        statusText = "\(title) 실행 중"
        syncRunningState(status: "\(title) 실행 중")
        appendLog("paper_id=\(paperID.isEmpty ? "-" : paperID)", workflowID: workflowID)
        Task {
            do {
                try await operation(workflowID)
                DispatchQueue.main.async {
                    self.finishWorkflow(workflowID, status: "완료", logLine: "done")
                }
            } catch {
                DispatchQueue.main.async {
                    let wasCancelled = self.jobs.first(where: { $0.id == workflowID })?.statusText == "중지됨"
                    self.finishWorkflow(
                        workflowID,
                        status: wasCancelled ? "중지됨" : "실패",
                        logLine: wasCancelled ? nil : "error: \(error.localizedDescription)"
                    )
                }
            }
        }
    }

    private func runCommand(_ arguments: [String], settings: RuntimeSettings, workflowID: UUID) async throws {
        DispatchQueue.main.async {
            self.beginCommand(arguments.first ?? "", workflowID: workflowID)
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            DispatchQueue.global(qos: .userInitiated).async {
                let script = URL(fileURLWithPath: settings.repoPath).appendingPathComponent("scripts/kpaper.py")
                guard FileManager.default.fileExists(atPath: script.path) else {
                    continuation.resume(throwing: AppError.message("scripts/kpaper.py not found at \(script.path)"))
                    return
                }
                guard let uv = Self.resolveUVExecutable() else {
                    continuation.resume(throwing: AppError.message("uv not found. Install uv and run uv sync in the repository first."))
                    return
                }

                let process = Process()
                process.currentDirectoryURL = URL(fileURLWithPath: settings.repoPath)
                process.executableURL = uv
                process.arguments = ["run", "scripts/kpaper.py"] + arguments

                var env = ProcessInfo.processInfo.environment
                env["UV_CACHE_DIR"] = ".uv-cache"
                if !settings.baseURLOverride.isEmpty {
                    env["OPENAI_BASE_URL"] = settings.baseURLOverride
                }
                if !settings.apiKeyOverride.isEmpty {
                    env["OPENAI_API_KEY"] = settings.apiKeyOverride
                }
                if let codex = Self.resolveCodexExecutable() {
                    env["CODEX_EXECUTABLE"] = codex.path
                    env = Self.environmentByAddingToolDirectories(env, tools: [uv, codex])
                } else {
                    env = Self.environmentByAddingToolDirectories(env, tools: [uv])
                }
                process.environment = env

                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = pipe
                var commandOutput = ""
                pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                    let data = handle.availableData
                    guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
                    commandOutput += text
                    DispatchQueue.main.async {
                        self?.appendLog(text.trimmingCharacters(in: .newlines), workflowID: workflowID)
                    }
                }

                DispatchQueue.main.async {
                    self.currentProcesses[workflowID] = process
                    let displayUV = uv.path.replacingOccurrences(of: settings.repoPath + "/", with: "")
                    self.appendLog("$ \(displayUV) run scripts/kpaper.py \(arguments.joined(separator: " "))", workflowID: workflowID)
                }

                do {
                    try process.run()
                    process.waitUntilExit()
                    pipe.fileHandleForReading.readabilityHandler = nil
                    DispatchQueue.main.async {
                        if self.currentProcesses[workflowID] === process {
                            self.currentProcesses[workflowID] = nil
                        }
                    }
                    if process.terminationStatus == 0 {
                        DispatchQueue.main.async {
                            if self.selectedWorkflowID == workflowID {
                                self.captureOutputPaths(from: commandOutput)
                            }
                        }
                        continuation.resume()
                    } else {
                        continuation.resume(throwing: AppError.message("command exited with status \(process.terminationStatus)"))
                    }
                } catch {
                    pipe.fileHandleForReading.readabilityHandler = nil
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func runCodexCLI(_ arguments: [String]) async throws -> String {
        guard let codex = Self.resolveCodexExecutable() else {
            throw AppError.message("Codex CLI를 찾을 수 없습니다. Codex를 설치한 뒤 다시 시도해주세요.")
        }
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = codex
                process.arguments = arguments
                process.environment = Self.environmentByAddingToolDirectories(
                    ProcessInfo.processInfo.environment,
                    tools: [codex]
                )
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = pipe
                DispatchQueue.main.async { self.currentProcess = process }
                do {
                    try process.run()
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()
                    DispatchQueue.main.async {
                        if self.currentProcess === process { self.currentProcess = nil }
                    }
                    let output = String(data: data, encoding: .utf8) ?? ""
                    guard process.terminationStatus == 0 else {
                        continuation.resume(throwing: AppError.message(output.isEmpty ? "Codex command failed" : output))
                        return
                    }
                    continuation.resume(returning: output)
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func captureOutputPaths(from text: String) {
        if let output = Self.lastRegexMatch(pattern: #""output"\s*:\s*"([^"]+\.ko\.paper\.html)""#, in: text) {
            lastKoreanOutput = output
        }
        if let bilingual = Self.lastRegexMatch(pattern: #""bilingual_output"\s*:\s*"([^"]+\.ko-en\.paper\.html)""#, in: text) {
            lastBilingualOutput = bilingual
        }
    }

    private func setRunning(_ running: Bool, status: String) {
        isRunning = running
        statusText = status
    }

    func selectWorkflow(_ workflowID: UUID) {
        guard let job = jobs.first(where: { $0.id == workflowID }) else { return }
        selectedWorkflowID = workflowID
        lastPaperID = job.paperID
        statusText = job.statusText
        progressCompleted = job.progressCompleted
        progressTotal = job.progressTotal
        progressLabel = job.progressLabel
        progressPhase = job.progressPhase
        progressDetail = job.progressDetail
        workflowStartedAt = job.startedAt
        logText = job.logText
    }

    private func finishWorkflow(_ workflowID: UUID, status: String, logLine: String?) {
        currentProcesses[workflowID] = nil
        updateJob(workflowID) { job in
            job.statusText = status
            job.isRunning = false
            if status == "완료" {
                job.progressPhase = "complete"
                job.progressCompleted = max(job.progressTotal, job.progressCompleted)
                job.progressLabel = "완료"
            }
            if let logLine { Self.append(logLine, to: &job.logText) }
        }
        if selectedWorkflowID == workflowID {
            statusText = status
            if let job = jobs.first(where: { $0.id == workflowID }) {
                logText = job.logText
                progressPhase = job.progressPhase
                progressCompleted = job.progressCompleted
                progressTotal = job.progressTotal
                progressLabel = job.progressLabel
                progressDetail = job.progressDetail
            }
        }
        syncRunningState(status: status)
    }

    private func syncRunningState(status: String) {
        isRunning = jobs.contains(where: \.isRunning)
        if !isRunning || selectedWorkflowID == nil {
            statusText = status
        }
    }

    private func updateJob(_ workflowID: UUID, change: (inout TranslationJob) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == workflowID }) else { return }
        change(&jobs[index])
    }

    private static func append(_ line: String, to text: inout String) {
        guard !line.isEmpty else { return }
        text = text.isEmpty ? line : text + "\n" + line
        if text.count > 120_000 {
            text.removeFirst(text.count - 120_000)
        }
    }

    private func appendLog(_ line: String) {
        guard !line.isEmpty else { return }
        updateProgress(from: line)
        if logText.isEmpty {
            logText = line
        } else {
            logText += "\n" + line
        }
        if logText.count > 120_000 {
            logText.removeFirst(logText.count - 120_000)
        }
    }

    private func appendLog(_ line: String, workflowID: UUID) {
        guard !line.isEmpty else { return }
        updateJob(workflowID) { job in
            Self.append(line, to: &job.logText)
            Self.updateProgress(from: line, job: &job)
        }
        guard selectedWorkflowID == workflowID else { return }
        updateProgress(from: line)
        Self.append(line, to: &logText)
    }

    private func beginCommand(_ command: String, workflowID: UUID) {
        let phase: String
        let label: String
        switch command {
        case "pdf-import":
            phase = "inspect"
            label = "PDF 검사 시작"
        case "translate":
            phase = "translate"
            label = "번역 준비 중"
        case "restyle":
            phase = "restyle"
            label = "문서 스타일 적용 중"
        default:
            phase = "preparing"
            label = "준비 중"
        }
        updateJob(workflowID) { job in
            job.progressPhase = phase
            job.progressCompleted = 0
            job.progressTotal = 0
            job.progressLabel = label
            job.progressDetail = ""
        }
        guard selectedWorkflowID == workflowID else { return }
        progressPhase = phase
        progressCompleted = 0
        progressTotal = 0
        progressLabel = label
        progressDetail = ""
    }

    private static func structuredProgress(from line: String) -> (String, Int, Int, String, String)? {
        let prefix = "KPAPER_PROGRESS "
        guard line.hasPrefix(prefix) else { return nil }
        let json = String(line.dropFirst(prefix.count))
        guard let data = json.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stage = payload["stage"] as? String,
              let label = payload["label"] as? String else {
            return nil
        }
        return (
            stage,
            payload["current"] as? Int ?? 0,
            payload["total"] as? Int ?? 0,
            label,
            payload["detail"] as? String ?? ""
        )
    }

    private func updateProgress(from text: String) {
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let line = String(rawLine)
            if let event = Self.structuredProgress(from: line) {
                progressPhase = event.0
                progressCompleted = event.1
                progressTotal = event.2
                progressLabel = event.3
                progressDetail = event.4
                continue
            }
            if let marker = line.range(of: "translating "),
               let total = Int(line[marker.upperBound...].split(separator: " ").first ?? "") {
                progressPhase = "translate"
                progressCompleted = 0
                progressTotal = total
                progressLabel = "번역 청크 0/\(total)"
                progressDetail = "모델 요청 준비 완료"
                continue
            }
            guard let marker = line.range(of: "completed batch ") else { continue }
            let batchToken = line[marker.upperBound...].split(separator: " ").first ?? ""
            let batchParts = batchToken.split(separator: "/")
            guard batchParts.count == 2, let total = Int(batchParts[1]) else { continue }
            progressTotal = total
            progressCompleted = min(total, progressCompleted + 1)
            progressPhase = "translate"
            progressLabel = "번역 청크 \(progressCompleted)/\(total)"
        }
    }

    private static func updateProgress(from text: String, job: inout TranslationJob) {
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let line = String(rawLine)
            if let event = structuredProgress(from: line) {
                job.progressPhase = event.0
                job.progressCompleted = event.1
                job.progressTotal = event.2
                job.progressLabel = event.3
                job.progressDetail = event.4
                continue
            }
            if let marker = line.range(of: "translating "),
               let total = Int(line[marker.upperBound...].split(separator: " ").first ?? "") {
                job.progressPhase = "translate"
                job.progressCompleted = 0
                job.progressTotal = total
                job.progressLabel = "번역 청크 0/\(total)"
                job.progressDetail = "모델 요청 준비 완료"
                continue
            }
            guard let marker = line.range(of: "completed batch ") else { continue }
            let batchToken = line[marker.upperBound...].split(separator: " ").first ?? ""
            let batchParts = batchToken.split(separator: "/")
            guard batchParts.count == 2, let total = Int(batchParts[1]) else { continue }
            job.progressTotal = total
            job.progressCompleted = min(total, job.progressCompleted + 1)
            job.progressPhase = "translate"
            job.progressLabel = "번역 청크 \(job.progressCompleted)/\(total)"
        }
    }

    private static func paperID(from url: URL) -> String {
        let raw = url.absoluteString
        if let range = raw.range(of: #"(\d{4}\.\d{4,5})v\d+"#, options: .regularExpression) {
            return "arxiv-" + raw[range].replacingOccurrences(of: #"v\d+$"#, with: "", options: .regularExpression)
                .replacingOccurrences(of: ".", with: "-")
        }
        let seed = [url.host ?? "paper", url.deletingPathExtension().lastPathComponent]
            .filter { !$0.isEmpty }
            .joined(separator: "-")
        return sanitizedID(from: seed)
    }

    private static func sanitizedID(from text: String) -> String {
        let lower = text.lowercased()
        let mapped = lower.map { char -> Character in
            if char.isLetter || char.isNumber {
                return char
            }
            return "-"
        }
        let collapsed = String(mapped).replacingOccurrences(of: #"-+"#, with: "-", options: .regularExpression)
        let trimmed = collapsed.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return trimmed.isEmpty ? "paper" : trimmed
    }

    private static func lastRegexMatch(pattern: String, in text: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            return nil
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        return regex.matches(in: text, range: range).last.flatMap { match in
            guard match.numberOfRanges > 1, let captureRange = Range(match.range(at: 1), in: text) else {
                return nil
            }
            return String(text[captureRange])
        }
    }

    private static func detectRepoPath() -> String? {
        let fileManager = FileManager.default
        let starts = [
            URL(fileURLWithPath: fileManager.currentDirectoryPath),
            Bundle.main.bundleURL,
            Bundle.main.resourceURL
        ].compactMap { $0 }

        for start in starts {
            var cursor = start.standardizedFileURL
            for _ in 0..<10 {
                let candidate = cursor.appendingPathComponent("kpaper")
                if fileManager.isExecutableFile(atPath: candidate.path) {
                    return cursor.path
                }
                let parent = cursor.deletingLastPathComponent()
                if parent.path == cursor.path { break }
                cursor = parent
            }
        }
        return nil
    }

    private static func isLiteParseRepo(atPath path: String) -> Bool {
        let root = URL(fileURLWithPath: path)
        let script = root.appendingPathComponent("scripts/kpaper.py")
        let manifest = root.appendingPathComponent("pyproject.toml")
        guard FileManager.default.isExecutableFile(atPath: root.appendingPathComponent("kpaper").path),
              FileManager.default.fileExists(atPath: script.path),
              FileManager.default.fileExists(atPath: manifest.path) else {
            return false
        }
        let scriptText = (try? String(contentsOf: script, encoding: .utf8)) ?? ""
        let manifestText = (try? String(contentsOf: manifest, encoding: .utf8)) ?? ""
        return scriptText.contains("load_liteparse") && manifestText.contains("liteparse")
    }

    private static func resolveUVExecutable() -> URL? {
        let fileManager = FileManager.default
        var candidates = ProcessInfo.processInfo.environment["PATH", default: ""]
            .split(separator: ":")
            .map { URL(fileURLWithPath: String($0)).appendingPathComponent("uv") }
        let home = fileManager.homeDirectoryForCurrentUser
        candidates += [
            home.appendingPathComponent(".local/bin/uv"),
            home.appendingPathComponent(".cargo/bin/uv"),
            URL(fileURLWithPath: "/opt/homebrew/bin/uv"),
            URL(fileURLWithPath: "/usr/local/bin/uv")
        ]
        for candidate in candidates where fileManager.isExecutableFile(atPath: candidate.path) {
            return candidate
        }
        return nil
    }

    private static func resolveCodexExecutable() -> URL? {
        let fileManager = FileManager.default
        var candidates = ProcessInfo.processInfo.environment["PATH", default: ""]
            .split(separator: ":")
            .map { URL(fileURLWithPath: String($0)).appendingPathComponent("codex") }
        let home = fileManager.homeDirectoryForCurrentUser
        let nodeVersions = home.appendingPathComponent(".nvm/versions/node")
        if let versions = try? fileManager.contentsOfDirectory(
            at: nodeVersions,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        ) {
            candidates += versions
                .sorted { $0.lastPathComponent > $1.lastPathComponent }
                .map { $0.appendingPathComponent("bin/codex") }
        }
        candidates += [
            home.appendingPathComponent(".local/bin/codex"),
            URL(fileURLWithPath: "/opt/homebrew/bin/codex"),
            URL(fileURLWithPath: "/usr/local/bin/codex")
        ]
        return candidates.first { fileManager.isExecutableFile(atPath: $0.path) }
    }

    private static func environmentByAddingToolDirectories(
        _ environment: [String: String],
        tools: [URL]
    ) -> [String: String] {
        var updated = environment
        var directories = tools.map { $0.deletingLastPathComponent().path }
        let existing = environment["PATH", default: ""]
            .split(separator: ":")
            .map(String.init)
        directories.append(contentsOf: existing)
        directories.append(contentsOf: ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])
        var seen = Set<String>()
        updated["PATH"] = directories.filter { !$0.isEmpty && seen.insert($0).inserted }.joined(separator: ":")
        return updated
    }

    private static func validateTranslationProvider(settings: RuntimeSettings) throws {
        if settings.provider == .codex {
            guard resolveCodexExecutable() != nil else {
                throw AppError.message("Codex CLI를 찾을 수 없습니다. Codex를 설치하고 ChatGPT로 로그인해주세요.")
            }
            return
        }
        try validateModelEndpoint(settings: settings)
    }

    private static func validateModelEndpoint(settings: RuntimeSettings) throws {
        let baseURL = effectiveBaseURL(settings: settings)
        guard !baseURL.isEmpty else {
            throw AppError.message("OpenAI Base URL이 비어 있습니다. 설정에 주소를 입력하거나 .env에 OPENAI_BASE_URL을 넣어주세요.")
        }
        guard let url = URL(string: baseURL), let host = url.host else {
            throw AppError.message("OpenAI Base URL 형식이 올바르지 않습니다: \(baseURL)")
        }
        let port = UInt16(url.port ?? (url.scheme == "https" ? 443 : 80))
        guard canReach(host: host, port: port, timeout: 2.0) else {
            throw AppError.message("OpenAI Base URL에 연결할 수 없습니다: \(baseURL)\n설정값 또는 .env의 OPENAI_BASE_URL을 확인해주세요.")
        }
    }

    private static func effectiveBaseURL(settings: RuntimeSettings) -> String {
        if !settings.baseURLOverride.isEmpty {
            return settings.baseURLOverride
        }
        return envValue(repoPath: settings.repoPath, key: "OPENAI_BASE_URL") ?? ""
    }

    private static func envValue(repoPath: String, key: String) -> String? {
        let envURL = URL(fileURLWithPath: repoPath).appendingPathComponent(".env")
        guard let contents = try? String(contentsOf: envURL, encoding: .utf8) else {
            return nil
        }
        for rawLine in contents.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty, !line.hasPrefix("#") else { continue }
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard parts.count == 2, parts[0].trimmingCharacters(in: .whitespacesAndNewlines) == key else {
                continue
            }
            return String(parts[1])
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
        }
        return nil
    }

    private static func canReach(host: String, port: UInt16, timeout: TimeInterval) -> Bool {
        guard let nwPort = NWEndpoint.Port(rawValue: port) else {
            return false
        }
        let semaphore = DispatchSemaphore(value: 0)
        let queue = DispatchQueue(label: "kpaper.endpoint-check")
        let connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .tcp)
        var isReady = false

        connection.stateUpdateHandler = { state in
            switch state {
            case .ready:
                isReady = true
                semaphore.signal()
            case .failed, .cancelled:
                semaphore.signal()
            default:
                break
            }
        }
        connection.start(queue: queue)
        _ = semaphore.wait(timeout: .now() + timeout)
        connection.cancel()
        return isReady
    }
}

enum AppError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value):
            return value
        }
    }
}
