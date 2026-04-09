// IFT.scala — Target-side IO bundle for the BOOM IFT bridge.
// Lives in bridgeinterfaces (no boom / chipyard dep) so GoldenGate can reference it.
// Uses raw UInts; the record format is defined in boom.v3.exu.IFTBridgeIO.

package firechip.bridgeinterfaces

import chisel3._

// Parameters passed as constructorArg from the bridge stub to GoldenGate.
case class IFTBridgeParams(
  retireWidth: Int,       // commit width (= BoomCoreParams.decodeWidth)
  recordBits:  Int = 256  // bits per IFT record (fixed at 256)
)

// One IFT tile output, flattened to raw UInts for the BlackBox boundary.
// retireWidth entries for commit records + 1 squash drain slot.
// clock is required by GoldenGate to identify the target clock domain.
class IFTBridgeTargetIO(params: IFTBridgeParams) extends Bundle {
  val clock         = Input(Clock())
  val commit_valid  = Input(UInt(params.retireWidth.W))        // arch_valid bits
  val commit_record = Input(Vec(params.retireWidth, UInt(params.recordBits.W)))
  val squash_valid  = Input(Bool())
  val squash_record = Input(UInt(params.recordBits.W))
  val squash_ovf    = Input(Bool())
}
