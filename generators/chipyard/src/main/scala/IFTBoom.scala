// IFTBoom.scala — Chipyard trait for exposing BOOM IFT tile IO at the system boundary.
//
// CanHaveBoomIFTIO collects BundleBridgeSink nodes from every BoomTile that has
// enableIFTBridge=true.  It also registers an InModuleBody (runs in DigitalTop scope)
// that creates Output IO ports connected to each sink bundle, storing them in
// _iftBoundaryIOs.  WithIFTPunchthrough (ChipTop scope) reads those DigitalTop-boundary
// ports — valid because ChipTop is the direct parent of DigitalTop.

package chipyard

import chisel3._
import org.chipsalliance.cde.config.Parameters
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.subsystem._

import boom.v3.common.{BoomTile}
import boom.v3.exu.IFTTileIO

// ---------------------------------------------------------------------------
// CanHaveBoomIFTIO
// ---------------------------------------------------------------------------
trait CanHaveBoomIFTIO { this: ChipyardSystem =>
  implicit val p: Parameters

  // Diplomacy-level: create sinks and connect to tile sources.
  val iftSinkNodes: Seq[(Int, BundleBridgeSink[IFTTileIO], Int)] =
    totalTiles.toSeq.collect {
      case (id, b: BoomTile) if b.boomParams.core.enableIFTBridge =>
        val sink = BundleBridgeSink[IFTTileIO]()
        sink := b.iftSourceNode.get
        (id.toInt, sink, b.boomParams.core.retireWidth)
    }.sortBy(_._1)

  // Hardware-level: Output IO ports created in DigitalTop's module scope.
  // Populated by the InModuleBody below; read by WithIFTPunchthrough in ChipTop scope.
  private val _iftBoundaryIOs = collection.mutable.ListBuffer[(Int, IFTTileIO, Int)]()
  def iftBoundaryIOs: Seq[(Int, IFTTileIO, Int)] = _iftBoundaryIOs.toSeq

  // Runs during DigitalTop module elaboration (before ChipTop's InModuleBody).
  // Creates one Output IO per tile and drives it from the sink bundle.
  InModuleBody {
    iftSinkNodes.foreach { case (id, sink, rw) =>
      val io = IO(Output(new IFTTileIO(rw)))
      io.suggestName(s"ift_tile_$id")
      io := sink.bundle
      _iftBoundaryIOs += ((id, io, rw))
    }
  }
}
