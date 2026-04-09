// IFTBridge.scala — Target-side BlackBox bridge stub for the BOOM IFT bridge.
// Instantiated inside BoomTile (via CanHaveBoomIFTIO + WithIFTVBridge) so GoldenGate
// sees it as part of the target and replaces it with IFTBridgeModule at compile time.

package firechip.bridgestubs

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config.Parameters

import firesim.lib.bridgeutils._
import firechip.bridgeinterfaces._

import boom.v3.exu.IFTTileIO

/** Bridge BlackBox for the BOOM IFT bridge.
  *
  * Converts IFTTileIO (BOOM-typed bundle) to the flattened IFTBridgeTargetIO used
  * by the GoldenGate implementation.  One bridge instance per tile.
  *
  * @param params  Bridge parameters (retireWidth, recordBits).
  */
class IFTBridge(params: IFTBridgeParams)
    extends BlackBox
    with Bridge[HostPortIO[IFTBridgeTargetIO]] {

  val moduleName    = "firechip.goldengateimplementations.IFTBridgeModule"
  val io            = IO(new IFTBridgeTargetIO(params))
  val bridgeIO      = HostPort(io)
  val constructorArg = Some(params)
  generateAnnotations()

  // Suffix defname by retireWidth to satisfy FIRRTL CheckHighForm when multiple
  // tiles use different retire widths (analogous to TracerVBridge's defnameSuffix).
  override def desiredName = super.desiredName + s"_${params.retireWidth}Wide"
}

object IFTBridge {
  /** Instantiate an IFTBridge and connect it to a tile's IFTTileIO.
    * Call from WithIFTVBridge HarnessBinder in firechip.chip.
    */
  def apply(tileIO: IFTTileIO)(implicit p: Parameters): IFTBridge = {
    val params  = IFTBridgeParams(retireWidth = tileIO.retireWidth)
    val bridge  = Module(new IFTBridge(params))
    bridge.io.clock         := chisel3.Module.clock
    // Convert Vec[Bool] → UInt bitmask for commit_valid
    bridge.io.commit_valid  := tileIO.commit_valid.asUInt
    bridge.io.commit_record := tileIO.commit_record
    bridge.io.squash_valid  := tileIO.squash_valid
    bridge.io.squash_record := tileIO.squash_record
    bridge.io.squash_ovf    := tileIO.squash_ovf
    bridge
  }
}
