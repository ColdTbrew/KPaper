import SwiftUI

struct ReleaseNotesView: View {
    private var lines: [String] {
        guard let url = Bundle.main.url(forResource: "ReleaseNotes", withExtension: "md"),
              let text = try? String(contentsOf: url, encoding: .utf8) else {
            return ["# 릴리스 반영사항", "반영사항 파일을 찾을 수 없습니다."]
        }
        return text.components(separatedBy: .newlines)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    if line.hasPrefix("### ") {
                        Text(String(line.dropFirst(4)))
                            .font(.title3.bold()).padding(.top, 12)
                    } else if line.hasPrefix("## ") {
                        Text(String(line.dropFirst(3)))
                            .font(.title2.bold()).padding(.top, 12)
                    } else if line.hasPrefix("# ") {
                        Text(String(line.dropFirst(2))).font(.largeTitle.bold())
                    } else if line.hasPrefix("- ") {
                        HStack(alignment: .top, spacing: 10) {
                            Text("•")
                            Text(.init(String(line.dropFirst(2))))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    } else if !line.isEmpty {
                        Text(.init(line)).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .textSelection(.enabled)
            .padding(28)
        }
        .frame(minWidth: 560, minHeight: 420)
    }
}

struct ReleaseNotesCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandGroup(after: .help) {
            Button("릴리스 반영사항") { openWindow(id: "release-notes") }
        }
    }
}
