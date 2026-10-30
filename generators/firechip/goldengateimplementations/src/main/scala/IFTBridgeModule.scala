// IFTBridgeModule.scala — GoldenGate implementation of the BOOM IFT bridge.
//
// Streams binary 256-bit IFT records to the host over a 512-bit DMA stream
// (2 records per beat).  Handles both committed (event_type=1) and
// speculatively-squashed (event_type=2) records that were packed in BoomCore.
//
// Stream format (512b / beat, little-endian):
//   bits[255:0]   record[0]  (lower-index or padding zero)
//   bits[511:256] record[1]  (higher-index)
//
// Compaction (D12, Task 3.7) and cross-cycle packing ([V3-PACK] SP-26, design D10): the valid records of a
// cycle are packed, in slot order (commit[0..retireWidth-1], then the squash/kill/STATE lane), into the lowest
// slots; with packAcrossCycles an odd last record waits in a one-record hold and shares its beat with the
// next cycle's first record, so the stream carries ceil(records/2) beats plus one zero-padded beat per flush
// (16 record-free target cycles, or idleLimit host cycles without a token).  packAcrossCycles = false sends
// ceil(k/2) beats per cycle, the pre-SP-26 behaviour.  The logic is firechip.bridgeinterfaces.IFTBeatPacker
// (tested by firechip.bridgestubs.IFTBeatPackerSpec, FT-11).  A zero (class 0) half is dropped by the driver.
//
// The C++ driver (ift_bridge.h / ift_bridge.cc) reads beats, splits them into
// 256-bit records, and writes them to a binary file for offline analysis.

package firechip.goldengateimplementations

import chisel3._

import org.chipsalliance.cde.config.Parameters

import midas.widgets._
import firesim.lib.bridgeutils._

import firechip.bridgeinterfaces._

class IFTBridgeModule(key: IFTBridgeParams)(implicit p: Parameters)
    extends BridgeModule[HostPortIO[IFTBridgeTargetIO]]()(p)
    with StreamToHostCPU {

  // Depth further reduced for FPGA LUT optimization (pointer fanout relief).
  // At 30 MHz × retireWidth=4 × 2 records/beat, peak is ~45M beats/sec; PCIe
  // Gen3 x16 drains at >300M beats/sec, so the queue is for host-driver poll
  // jitter, not sustained bandwidth.  512 entries = ~5.7 µs of buffering,
  // sufficient for typical 1–5 µs MMIO poll cadences.  Reducing 2048→512
  // consolidates URAM placement (~16 → ~4 URAMs), shortens enq_ptr broadcast
  // routing, and drops two bits off the Counter-based pointer — relieving the
  // many [Physopt 32-1132] very-high-fanout warnings Vivado reported on
  // enq_ptr_value_reg[*]_rep__* replicas.  If host polling falls behind in
  // practice, bump to 1024; do not drop below 256.
  val toHostCPUQueueDepth = 512

  lazy val module = new BridgeModuleImp(this) {
    val io    = IO(new WidgetIO)
    val hPort = IO(HostPort(new IFTBridgeTargetIO(key)))

    val retireWidth   = key.retireWidth
    val recordBits    = key.recordBits   // 256
    val recordsPerBeat = BridgeStreamConstants.streamWidthBits / recordBits  // 512/256=2
    val totalRecords   = retireWidth + 1 // commit slots + 1 squash slot
    val numArms        = (totalRecords + recordsPerBeat - 1) / recordsPerBeat

    // Collect all records from the target port.
    val allRecords = Wire(Vec(totalRecords, UInt(recordBits.W)))
    val allValids  = Wire(Vec(totalRecords, Bool()))
    for (w <- 0 until retireWidth) {
      allRecords(w) := hPort.hBits.commit_record(w)
      allValids(w)  := hPort.hBits.commit_valid(w)
    }
    // [LANEGATE] D2a/D12 (Task 2.9, Bug 5).  An arm is shipped whole whenever any record in it
    // (or in a later arm) is valid, so invalid commit lanes reach the host inside a valid
    // beat.  They carry event_type 0 since Task 2.4 (rob.scala com_evt), and the driver drops
    // event_type 0 (ift_bridge.cc tick()), so no fake commit record reaches the file.  This
    // check keeps the two halves of that contract in step: a lane's valid bit must equal
    // "its record's event_type is non-zero".  Checked only on a valid input token (hBits is
    // meaningful only then).  Simulation-only (firtool guards it out of synthesis); no FPGA
    // logic.  Compaction of the valid records into the lowest beats is Task 3.7 (below); this
    // check stays on the uncompacted lanes because it checks the core's contract.
    // The squash slot is deliberately NOT checked: the squash FIFO's deq.bits are stale when
    // deq.valid is low.  That is harmless because the compaction below selects a record only
    // when its valid bit is set, so a stale squash record never reaches the stream.
    when (hPort.toHost.hValid) {
      for (w <- 0 until retireWidth) {
        assert(hPort.hBits.commit_valid(w) === hPort.hBits.commit_record(w)(1, 0).orR,
               s"[IFTBridge] commit lane $w: commit_valid disagrees with record event_type (D2a)")
      }
    }
    // squash_ovf is now always false (rob.scala retains non-winning banks' pending bits
    // instead of dropping records), so pass the squash record through unmodified.
    allRecords(retireWidth) := hPort.hBits.squash_record
    allValids(retireWidth)  := hPort.hBits.squash_valid

    // [V3-PACK] SP-26 (design D10, owner "okay. do this"): compaction ([BRIDGECOMPACT], unchanged) plus
    // cross-cycle packing in IFTBeatPacker.  The handshake is the one this block had before: a token fires
    // only in the cycle its last beat is enqueued (or at once when it has no beat), and every enqueue/fire
    // needs toHost.hValid, fromHost.hReady and streamEnq.ready.  Host-side logic only: the target token is
    // unchanged.  packAcrossCycles = false reproduces the pre-SP-26 beats exactly (FT-11).
    val packAcrossCycles = true
    val packer = Module(new IFTBeatPacker(totalRecords, recordBits, packAcrossCycles))
    require(recordsPerBeat == 2 && packer.beatBits == BridgeStreamConstants.streamWidthBits,
            "IFTBridgeModule: two 256-bit records per 512-bit beat")
    require(packer.maxBeats >= numArms, "IFTBridgeModule: the packer must cover every arm of a token")
    packer.io.in.valid        := hPort.toHost.hValid && hPort.fromHost.hReady
    packer.io.in.bits.valids  := allValids
    packer.io.in.bits.records := allRecords
    packer.io.out.ready       := streamEnq.ready

    streamEnq.bits  := packer.io.out.bits
    streamEnq.valid := packer.io.out.valid

    hPort.toHost.hReady   := packer.io.in.ready && hPort.fromHost.hReady
    hPort.fromHost.hValid := packer.io.in.ready && hPort.toHost.hValid

    genCRFile()

    override def genHeader(base: BigInt, memoryRegions: Map[String, BigInt], sb: StringBuilder): Unit = {
      genConstructor(
        base,
        sb,
        "ift_bridge_t",
        "ift_bridge",
        Seq(
          UInt32(toHostStreamIdx),
          UInt32(toHostCPUQueueDepth),
          UInt32(retireWidth),
          UInt32(recordBits),
          Verbatim(clockDomainInfo.toC),
        ),
        hasStreams = true,
      )
    }
  }
}
