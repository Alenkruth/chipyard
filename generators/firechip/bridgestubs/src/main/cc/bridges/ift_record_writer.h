// ift_record_writer.h — [V3-DRAIN] SP-25: record sink of the IFT bridge driver with one writer thread.
//
// The tick thread copies each valid 32-byte record into the current buffer of a ring of `nbufs` buffers of
// `buf_bytes` bytes (driver: 4 x 4 MB).  A full buffer is handed to ONE writer thread, which writes it with a
// single fwrite (the FILE is unbuffered, setvbuf _IONBF, so that is one write(2) of buf_bytes).  The tick
// thread blocks only when it needs a new buffer and all the others are queued or being written; the blocked
// time is counted in block_ns.  flush() submits the partial buffer and waits until every buffer is on disk;
// close() also stops and joins the writer.  Records reach the file in append order, unchanged.
// Header-only and free of FireSim headers, so the offline harness (FT-10) exercises this exact code.
#pragma once

#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

class ift_record_writer_t {
public:
  static constexpr size_t kRecordBytes = 32;

  explicit ift_record_writer_t(size_t nbufs = 4, size_t buf_bytes = size_t(4) << 20)
      : nbufs_(nbufs < 2 ? 2 : nbufs),
        buf_bytes_((buf_bytes / kRecordBytes) * kRecordBytes) {}
  ~ift_record_writer_t() { close(); }
  ift_record_writer_t(const ift_record_writer_t &) = delete;
  ift_record_writer_t &operator=(const ift_record_writer_t &) = delete;

  // Start the writer thread on an open stream (the caller owns and closes the FILE).  Returns false if the
  // buffers cannot be allocated (then append() writes through with fwrite, as before SP-25).
  bool start(FILE *f) {
    out_ = f;
    for (size_t i = 0; i < nbufs_; ++i) {
      void *p = std::aligned_alloc(4096, ((buf_bytes_ + 4095) / 4096) * 4096);
      if (!p) {
        fprintf(stderr, "[IFTBridge] WARNING: writer buffer allocation failed; writing synchronously\n");
        release_buffers();
        return false;
      }
      bufs_.push_back(static_cast<uint8_t *>(p));
    }
    for (size_t i = 1; i < nbufs_; ++i) free_.push_back(i);
    cur_ = 0;
    fill_ = 0;
    stop_ = false;
    thread_ = std::thread([this] { writer_loop(); });
    return true;
  }

  // Tick thread: one valid 32-byte record.
  inline void append(const void *rec) {
    if (bufs_.empty()) {                       // no ring (start() failed or not started): write through
      if (out_ && fwrite(rec, kRecordBytes, 1, out_) != 1) note_error();
      ++records;
      return;
    }
    if (fill_ + kRecordBytes > buf_bytes_) submit_current();
    std::memcpy(bufs_[cur_] + fill_, rec, kRecordBytes);
    fill_ += kRecordBytes;
    ++records;
  }

  // Submit the partial buffer and wait until every submitted buffer has been written.
  void flush() {
    if (bufs_.empty()) return;
    if (fill_ != 0) submit_current();
    std::unique_lock<std::mutex> lk(m_);
    cv_free_.wait(lk, [this] { return full_.empty() && in_flight_ == 0; });
  }

  // flush(), then stop and join the writer thread.  Idempotent.
  void close() {
    if (bufs_.empty()) return;
    flush();
    {
      std::lock_guard<std::mutex> lk(m_);
      stop_ = true;
    }
    cv_full_.notify_all();
    if (thread_.joinable()) thread_.join();
    release_buffers();
  }

  size_t nbufs() const { return nbufs_; }
  size_t buf_bytes() const { return buf_bytes_; }

  // Statistics.  records / block_ns / submits are tick-thread values; writer_ns / buffers_written /
  // write_errors / max_queued are complete after close().
  uint64_t records = 0;
  uint64_t submits = 0;
  uint64_t block_ns = 0;
  uint64_t writer_ns = 0;
  uint64_t buffers_written = 0;
  uint64_t write_errors = 0;
  size_t max_queued = 0;

private:
  static uint64_t now_ns() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
  }

  void note_error() {
    if (write_errors++ == 0)
      fprintf(stderr, "[IFTBridge] ERROR: write to the output file failed (errno %d: %s)\n", errno,
              std::strerror(errno));
  }

  void submit_current() {
    std::unique_lock<std::mutex> lk(m_);
    full_.emplace_back(cur_, fill_);
    if (full_.size() > max_queued) max_queued = full_.size();
    ++submits;
    cv_full_.notify_one();
    if (free_.empty()) {
      const uint64_t t0 = now_ns();
      cv_free_.wait(lk, [this] { return !free_.empty(); });
      block_ns += now_ns() - t0;
    }
    cur_ = free_.front();
    free_.pop_front();
    fill_ = 0;
  }

  void writer_loop() {
    std::unique_lock<std::mutex> lk(m_);
    for (;;) {
      cv_full_.wait(lk, [this] { return !full_.empty() || stop_; });
      if (full_.empty()) return;               // stop_ and nothing left
      const std::pair<size_t, size_t> job = full_.front();
      full_.pop_front();
      ++in_flight_;
      lk.unlock();
      const uint64_t t0 = now_ns();
      const size_t n = job.second ? fwrite(bufs_[job.first], 1, job.second, out_) : 0;
      const uint64_t dt = now_ns() - t0;
      lk.lock();
      writer_ns += dt;
      if (n != job.second) note_error();
      ++buffers_written;
      --in_flight_;
      free_.push_back(job.first);
      cv_free_.notify_all();
    }
  }

  void release_buffers() {
    for (uint8_t *p : bufs_) std::free(p);
    bufs_.clear();
    free_.clear();
    full_.clear();
  }

  const size_t nbufs_;
  const size_t buf_bytes_;
  FILE *out_ = nullptr;
  std::vector<uint8_t *> bufs_;
  std::deque<size_t> free_;
  std::deque<std::pair<size_t, size_t>> full_;   // (buffer, bytes) waiting for the writer
  size_t cur_ = 0;
  size_t fill_ = 0;
  size_t in_flight_ = 0;
  bool stop_ = false;
  std::mutex m_;
  std::condition_variable cv_full_;
  std::condition_variable cv_free_;
  std::thread thread_;
};
