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
// Arm grouping for a retireWidth=4 core (5 total records: 4 commit + 1 squash):
//   arm[0]: commit[0], commit[1]
//   arm[1]: commit[2], commit[3]
//   arm[2]: squash,    zero-pad
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

  // Depth reduced for FPGA LUT optimization (pointer fanout reduction).
  // At 25 MHz × retireWidth=4, peak = 100M records/sec; PCIe drains ~312M/sec.
  // 2048 entries = ~20 µs of buffering — sufficient for host-driver jitter.
  val toHostCPUQueueDepth = 2048

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
    // squash_ovf is now always false (rob.scala retains non-winning banks' pending bits
    // instead of dropping records), so pass the squash record through unmodified.
    allRecords(retireWidth) := hPort.hBits.squash_record
    allValids(retireWidth)  := hPort.hBits.squash_valid

    // Group records into arms of recordsPerBeat (= 2) entries.
    // For each arm: pad short arms with zero-record.
    val armIndices: Seq[Seq[Int]] = (0 until totalRecords).grouped(recordsPerBeat).toSeq
    // 512-bit stream bits for each arm (pad to streamWidthBits)
    val allStreamBits: Seq[UInt] = armIndices.map { idxGroup =>
      val recs = idxGroup.padTo(recordsPerBeat, -1)  // -1 → zero padding
      val parts: Seq[UInt] = recs.reverse.map {
        case -1 => 0.U(recordBits.W)
        case k  => allRecords(k)
      }
      Cat(parts).pad(BridgeStreamConstants.streamWidthBits)
    }

    // anyValid(armIdx) = true if any record in that arm is valid.
    val anyValid: Seq[Bool] = armIndices.map(idxGroup =>
      idxGroup.map(k => allValids(k)).reduce(_ || _)
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
