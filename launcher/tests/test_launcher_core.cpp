// Minimal self-contained tests (no framework needed): run the binary, non-zero exit = failure.
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>

#include "launcher_core.hpp"

namespace fs = std::filesystem;
using namespace launcher;

static int failures = 0;
#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                   \
        }                                                                 \
    } while (0)

static void touch(const fs::path& p) {
    fs::create_directories(p.parent_path());
    std::ofstream(p) << "x";
}

static fs::path make_app(const fs::path& root, const std::string& name, bool complete = true,
                         bool exe = true) {
    fs::path dir = root / name;
    fs::create_directories(dir);
    if (complete) touch(dir / kCompleteMarker);
    if (exe) touch(dir / kAppExe);
    return dir;
}

static void test_parse_version() {
    CHECK(parse_version("1.2.3") == (Version{1, 2, 3}));
    CHECK(parse_version("0.10.0") == (Version{0, 10, 0}));
    CHECK(!parse_version("1.2"));
    CHECK(!parse_version("1.2.3.4"));
    CHECK(!parse_version("01.2.3"));
    CHECK(!parse_version("1.2.3-beta"));
    CHECK(!parse_version("a.b.c"));
    CHECK(!parse_version(""));
    CHECK(!parse_version("1234567890.0.0"));
    CHECK((Version{1, 10, 0}) > (Version{1, 9, 9}));  // numeric, not string, comparison
}

static void test_scan_and_pick(const fs::path& tmp) {
    fs::path root = tmp / "install";
    make_app(root, "app-1.9.0");
    make_app(root, "app-1.10.0");                     // newest usable (numeric order)
    make_app(root, "app-2.0.0", /*complete=*/false);  // interrupted update
    make_app(root, "app-3.0.0");
    touch(root / "app-3.0.0" / kDisabledMarker);      // known bad
    make_app(root, "app-4.0.0", true, /*exe=*/false);  // broken package
    make_app(root, "app-x.y.z");                      // ignored: not a version
    make_app(root, "other");                          // ignored: no prefix
    touch(root / "app-9.9.9");                        // ignored: a file, not a folder
    fs::create_directories(root / u8"工具資料");      // non-ASCII names must not break the scan

    auto candidates = scan(root);
    CHECK(candidates.size() == 5);
    CHECK(candidates.front().version == (Version{4, 0, 0}));
    CHECK(candidates[0].skip_reason.find("missing") == 0);
    CHECK(candidates[1].skip_reason == "disabled");
    CHECK(candidates[2].skip_reason.find("incomplete") == 0);

    auto chosen = pick(candidates);
    CHECK(chosen.has_value());
    CHECK(chosen && chosen->version == (Version{1, 10, 0}));
    CHECK(chosen && chosen->dir == root / "app-1.10.0");
}

static void test_nothing_usable(const fs::path& tmp) {
    fs::path root = tmp / "empty";
    fs::create_directories(root);
    CHECK(!pick(scan(root)));
    make_app(root, "app-1.0.0", false);
    CHECK(!pick(scan(root)));
    CHECK(scan(tmp / "does-not-exist").empty());
}

static void test_command_line() {
    CHECK(args_after_program(L"\"C:\\Program Files\\ToolManager.exe\" --browser") == L"--browser");
    CHECK(args_after_program(L"ToolManager.exe   a \"b c\"") == L"a \"b c\"");
    CHECK(args_after_program(L"\"C:\\x y\\t.exe\"") == L"");
    CHECK(args_after_program(L"t.exe") == L"");
    CHECK(args_after_program(L"\"C:\\x y\\t.exe\"\t--catalog \"D:\\my catalog\"") ==
          L"--catalog \"D:\\my catalog\"");

    CHECK(quote_arg(L"C:\\simple\\a.exe") == L"C:\\simple\\a.exe");
    CHECK(quote_arg(L"C:\\Users\\王 小明\\app.exe") == L"\"C:\\Users\\王 小明\\app.exe\"");
    CHECK(quote_arg(L"C:\\dir with space\\") == L"\"C:\\dir with space\\\\\"");
    CHECK(quote_arg(L"say \"hi\"") == L"\"say \\\"hi\\\"\"");
    CHECK(quote_arg(L"") == L"\"\"");
}

int main() {
    fs::path tmp = fs::temp_directory_path() / ("tm-launcher-test-" + std::to_string(std::rand()));
    fs::remove_all(tmp);
    fs::create_directories(tmp);
    test_parse_version();
    test_scan_and_pick(tmp);
    test_nothing_usable(tmp);
    test_command_line();
    fs::remove_all(tmp);
    if (failures) {
        std::fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    std::puts("all launcher tests passed");
    return 0;
}
