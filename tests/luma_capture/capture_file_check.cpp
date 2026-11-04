// Prints a capture the way luma::capture::Reader sees it, for test_cpp_reader.py to compare with Python.
#include <cstdio>
#include <string>

#include "luma/capture_file.hpp"

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  std::string error;
  auto reader = luma::capture::Reader::open(argv[1], &error);
  if (!reader) {
    std::printf("error %s\n", error.c_str());
    return 1;
  }
  std::printf("file %llu %u\n", static_cast<unsigned long long>(reader->start_ns()), reader->bus());
  luma::capture::Record record;
  while (reader->next(record)) {
    unsigned long sum = 0;
    for (auto byte : record.data) sum = sum * 31 + byte;
    std::printf("%llu %u %c %u %02x %d %u %zu %d %d %lu\n", static_cast<unsigned long long>(record.t_ns), record.kind,
                record.event ? record.event : '-', record.dev, record.ep, record.status, record.urb_len,
                record.data.size(), record.replayable() ? 1 : 0, record.host_write() ? 1 : 0, sum);
  }
  std::printf("truncated %d\n", reader->truncated() ? 1 : 0);
}
