// IFTBeatPacker.scala — [V3-PACK] SP-26: compaction + cross-cycle packing of the IFT bridge records.
//
// Host-side logic of IFTBridgeModule (GoldenGate), kept here (bridgeinterfaces: plain Chisel, no FireSim or
// boom dependency) so the same source is compiled by GoldenGate (chisel3 3.6) and tested by chiseltest under
// Chipyard (Chisel 6): firechip.bridgestubs.IFTBeatPackerSpec.  Only chisel3._ and chisel3.util._ basics are
// used (no MuxLookup, no DecoupledIO.fire) so both Chisel versions accept it.
//
// One input token = one target cycle: `nSlots` records with valid bits (commit lanes, then the
// squash/kill/STATE lane).  The valid records are compacted into the lowest slots in slot order (the
// [BRIDGECOMPACT] network of Task 3.7, unchanged), giving k records.
//   packAcrossCycles = false: the token is sent as ceil(k/2) beats, an odd tail padded with a zero record --
//     exactly the beats IFTBridgeModule sent before SP-26.
//   packAcrossCycles = true: a one-record `hold` register (record + valid + 4-bit age) joins the stream.  The
//     token's sequence is [hold if valid] ++ its k records (n records); floor(n/2) beats leave in order and
//     an odd last record becomes the new hold, so two records share a beat across target cycles.  The hold
//     leaves zero-padded (a) at the 16th token in a row without a record (age), or (b) after `idleLimit` host
//     cycles with no input token (end of run, STEP pauses), so no record waits indefinitely; it is never
//     dropped.  A token fires only in the cycle its last beat is enqueued (or at once with no beat), as before.
// The packer never back-pressures the target beyond what the stream queue already does (in.ready =
// "last beat enqueued", the same rule as before), and it changes nothing on the target side.
package firechip.bridgeinterfaces

import chisel3._
import chisel3.util._

class IFTBeatPackerToken(val nSlots: Int, val recordBits: Int) extends Bundle {
  val valids  = Vec(nSlots, Bool())
  val records = Vec(nSlots, UInt(recordBits.W))
}

class IFTBeatPacker(val nSlots: Int, val recordBits: Int, val packAcrossCycles: Boolean,
                    val ageLimit: Int = 16, val idleLimit: Int = 256) extends Module {
  require(nSlots >= 1 && recordBits >= 4, "IFTBeatPacker: bad geometry")
  require(ageLimit >= 1 && ageLimit <= 16, "IFTBeatPacker: ageLimit must fit the 4-bit age")
  require(idleLimit >= 1 && idleLimit <= 65536, "IFTBeatPacker: idleLimit out of range")
  val beatBits = 2 * recordBits
  // beats one token can need: ceil(nSlots/2) without packing, ceil((nSlots+1)/2) with the hold in front
  val maxBeats = if (packAcrossCycles) (nSlots + 2) / 2 else (nSlots + 1) / 2

  val io = IO(new Bundle {
    val in        = Flipped(Decoupled(new IFTBeatPackerToken(nSlots, recordBits)))
    val out       = Decoupled(UInt(beatBits.W))
    val holdValid = Output(Bool())   // observability for the test; unconnected in the bridge
  })

  // [BRIDGECOMPACT] D12 (Task 3.7), moved from IFTBridgeModule unchanged: prefix(s) = number of valid records
  // below slot s, so a valid slot s lands in packed slot prefix(s); at most one slot selects each destination,
  // so Mux1H is exact and an unselected destination is zero.
  val prefix = (0 until nSlots).map(i => PopCount(io.in.bits.valids.take(i)))
  val packed = Wire(Vec(nSlots, UInt(recordBits.W)))
  for (d <- 0 until nSlots) {
    val sel = (d until nSlots).map(s => io.in.bits.valids(s) && prefix(s) === d.U)
    packed(d) := Mux1H(sel, (d until nSlots).map(io.in.bits.records(_)))
  }
  val k = PopCount(io.in.bits.valids)

  val counter = RegInit(0.U(log2Ceil(maxBeats + 1).W))
  val holdV: Bool   = if (packAcrossCycles) RegInit(false.B) else false.B
  val hold: UInt    = if (packAcrossCycles) Reg(UInt(recordBits.W)) else 0.U(recordBits.W)
  val holdAge: UInt = if (packAcrossCycles) RegInit(0.U(4.W)) else 0.U(4.W)
  val idleW         = log2Ceil(idleLimit + 1)
  val idleCnt: UInt = if (packAcrossCycles) RegInit(0.U(idleW.W)) else 0.U(idleW.W)

  // The token's record sequence: [hold] ++ packed, zero beyond its n records.
  val seqLen = 2 * maxBeats
  val seq = Wire(Vec(seqLen, UInt(recordBits.W)))
  for (i <- 0 until seqLen) {
    val direct  = if (i < nSlots) packed(i) else 0.U(recordBits.W)
    val shifted = if (i == 0) hold else if (i - 1 < nSlots) packed(i - 1) else 0.U(recordBits.W)
    seq(i) := (if (packAcrossCycles) Mux(holdV, shifted, direct) else direct)
  }
  val n        = k +& holdV.asUInt
  val flushAge = if (packAcrossCycles) holdV && k === 0.U && holdAge === (ageLimit - 1).U else false.B
  val pad      = (!packAcrossCycles).B || flushAge           // send an odd tail now, zero-padded
  val beats    = (n +& pad.asUInt) >> 1
  val maybeEnq  = counter < beats
  val maybeFire = !maybeEnq || counter === (maxBeats - 1).U
  val idleFlush = if (packAcrossCycles) !io.in.valid && holdV && counter === 0.U && idleCnt === (idleLimit - 1).U
                  else false.B

  val beatSel = (0 until maxBeats).map(j => counter === j.U)
  val beatBus = (0 until maxBeats).map(j => Cat(seq(2 * j + 1), seq(2 * j)))
  io.out.valid := idleFlush || (maybeEnq && io.in.valid)
  io.out.bits  := Mux(idleFlush, Cat(0.U(recordBits.W), hold), Mux1H(beatSel, beatBus))
  io.in.ready  := maybeFire && io.out.ready
  io.holdValid := holdV

  val inFire  = io.in.valid && io.in.ready
  val outFire = io.out.valid && io.out.ready
  when (inFire) {
    counter := 0.U
  } .elsewhen (outFire && !idleFlush) {
    counter := counter + 1.U
  }

  if (packAcrossCycles) {
    val keepTail = n(0) && !pad                                // odd tail stays for the next token
    val tail     = Mux1H((1 to seqLen).map(v => n === v.U), (0 until seqLen).map(seq(_)))
    when (inFire) {
      holdV   := keepTail
      when (keepTail) { hold := tail }
      holdAge := Mux(keepTail && holdV && k === 0.U, holdAge + 1.U, 0.U)
    } .elsewhen (idleFlush && io.out.ready) {
      holdV   := false.B
      holdAge := 0.U
    }
    idleCnt := Mux(io.in.valid || !holdV || counter =/= 0.U, 0.U,
               Mux(idleCnt === (idleLimit - 1).U, idleCnt, idleCnt + 1.U))
    when (idleFlush && io.out.ready) { idleCnt := 0.U }
  }
}
