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
// Compaction (D12, Task 3.7): the valid records of a cycle are packed, in slot order
// (commit[0..retireWidth-1], then squash), into the lowest slots of "packed".  Arm j is
// packed[2j], packed[2j+1].  A cycle with k valid records therefore takes ceil(k/2) beats.
// For a retireWidth=4 core (5 slots: 4 commit + 1 squash), k = 1..2 takes 1 beat, k = 3..4
// takes 2 beats and k = 5 takes 3 beats.  The squash record may share a beat with a commit
// record.  An unused slot of the last beat is zero (event_type 0), which the driver drops.
//
// The C++ driver (ift_bridge.h / ift_bridge.cc) reads beats, splits them into
// 256-bit records, and writes them to a binary file for offline analysis.

package firechip.goldengateimplementations

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config.Parameters

import midas.widgets._
import firesim.lib.bridgeutils._
import freechips.rocketchip.util.DecoupledHelper

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

    // [BRIDGECOMPACT] D12 (Task 3.7).  Pack the valid records into the lowest slots, keeping
    // slot order.  prefix(s) = number of valid records below slot s, so a valid slot s lands
    // in packed slot prefix(s).  At most one slot selects each destination d (prefix is
    // strictly increasing over the valid slots), so Mux1H is exact and an unselected
    // destination is zero (event_type 0, dropped by the driver).  Only s >= d can select d
    // (prefix(s) <= s), so the other terms are not built.  Host-side logic only: the
    // target token is unchanged, and the prefix counts are 4-input popcounts.
    val prefix = (0 until totalRecords).map(i => PopCount(allValids.take(i)))
    val packed  = Wire(Vec(totalRecords, UInt(recordBits.W)))
    val packedV = Wire(Vec(totalRecords, Bool()))
    for (d <- 0 until totalRecords) {
      val sel = (d until totalRecords).map(s => allValids(s) && prefix(s) === d.U)
      packedV(d) := sel.reduce(_ || _)
      packed(d)  := Mux1H(sel, (d until totalRecords).map(allRecords(_)))
    }

    // Group the packed records into arms of recordsPerBeat (= 2) entries.
    // For each arm: pad short arms with zero-record.
    val armIndices: Seq[Seq[Int]] = (0 until totalRecords).grouped(recordsPerBeat).toSeq
    // 512-bit stream bits for each arm (pad to streamWidthBits)
    val allStreamBits: Seq[UInt] = armIndices.map { idxGroup =>
      val recs = idxGroup.padTo(recordsPerBeat, -1)  // -1 → zero padding
      val parts: Seq[UInt] = recs.reverse.map {
        case -1 => 0.U(recordBits.W)
        case k  => packed(k)
      }
      Cat(parts).pad(BridgeStreamConstants.streamWidthBits)
    }

    // anyValid(armIdx) = true if any packed record in that arm is valid.  Because the valid
    // records are packed from slot 0 up, this is packedV of the arm's first slot.
    val anyValid: Seq[Bool] = armIndices.map(idxGroup =>
      packedV(idxGroup.head)
    )

    // anyValidRemain(armIdx) = OR of anyValid for arms >= armIdx.
    val anyValidRemain: Seq[Bool] = Seq.tabulate(numArms) { idx =>
      (idx until numArms).map(anyValid(_)).reduce(_ || _)
    }

    // Counter selects which arm to drain.
    val counterBits = log2Ceil(numArms + 1)
    val counter     = RegInit(0.U(counterBits.W))

    val streamMux         = MuxLookup(counter, allStreamBits(0), Seq.tabulate(numArms)(x => x.U -> allStreamBits(x)))
    val anyValidRemainMux = MuxLookup(counter, false.B, Seq.tabulate(numArms)(x => x.U -> anyValidRemain(x)))

    // A token fires (consumed) once we've passed all arms with valid data.
    val maybeFire = !anyValidRemainMux || (counter === (numArms - 1).U)
    val maybeEnq  = anyValidRemainMux

    val commonPredicates = Seq(hPort.toHost.hValid, hPort.fromHost.hReady, streamEnq.ready)
    val do_enq_helper  = DecoupledHelper((maybeEnq +: commonPredicates):_*)
    val do_fire_helper = DecoupledHelper((maybeFire +: commonPredicates):_*)

    when (do_fire_helper.fire()) {
      counter := 0.U
    } .elsewhen (do_enq_helper.fire()) {
      counter := counter + 1.U
    }

    streamEnq.bits  := streamMux
    streamEnq.valid := do_enq_helper.fire(streamEnq.ready)

    hPort.toHost.hReady   := do_fire_helper.fire(hPort.toHost.hValid)
    hPort.fromHost.hValid := do_fire_helper.fire(hPort.fromHost.hReady)

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
