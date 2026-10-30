// IFTBeatPackerSpec.scala — [V3-PACK] SP-26 / FT-11: IFTBeatPacker against a cycle model and the stream rules.
//
// Run (Chipyard sbt; chiseltest 6.0.0 reaches this project through chipyard -> dsptools, build.sbt:414):
//   java -jar scripts/sbt-launch.jar ... "project firechip_bridgestubs" "testOnly firechip.bridgestubs.IFTBeatPackerSpec"
// Every case drives 2,000 random tokens (0-5 valid records, random slots, invalid slots carry junk with class 0),
// random idle gaps (0-40 host cycles), random stream back-pressure, and checks
//   (1) the DUT equals the cycle model below in every cycle (out.valid, in.ready, out.bits when valid);
//   (2) the non-zero records of the emitted beats, low half first, are exactly the tokens' valid records in
//       slot order (order kept, no loss, no duplicate, no junk);
//   (3) packing on: a zero half only in a beat sent by the age flush or the idle flush;
//       packing off: the beats equal today's beats (ceil(k/2) per token, zero-padded odd tail).
package firechip.bridgestubs

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

import firechip.bridgeinterfaces.IFTBeatPacker

class IFTBeatPackerModel(nSlots: Int, recordBits: Int, pack: Boolean, ageLimit: Int, idleLimit: Int) {
  val maxBeats = if (pack) (nSlots + 2) / 2 else (nSlots + 1) / 2
  var counter = 0
  var holdV = false
  var hold = BigInt(0)
  var age = 0
  var idle = 0
  // returns (outValid, outBits, inReady, reason) and advances one cycle with outReady
  def step(inValid: Boolean, valids: Seq[Boolean], records: Seq[BigInt], outReady: Boolean): (Boolean, BigInt, Boolean, String) = {
    val packed = (valids zip records).filter(_._1).map(_._2).padTo(nSlots, BigInt(0))
    val k = valids.count(identity)
    val hv = pack && holdV
    val seq = (if (hv) hold +: packed else packed).padTo(2 * maxBeats, BigInt(0))
    val n = k + (if (hv) 1 else 0)
    val flushAge = pack && holdV && k == 0 && age == ageLimit - 1
    val pad = !pack || flushAge
    val beats = (n + (if (pad) 1 else 0)) / 2
    val maybeEnq = counter < beats
    val maybeFire = !maybeEnq || counter == maxBeats - 1
    val idleFlush = pack && !inValid && holdV && counter == 0 && idle == idleLimit - 1
    val outValid = idleFlush || (maybeEnq && inValid)
    val (outBits, reason) =
      if (idleFlush) (hold, "idle")
      else if (counter < maxBeats) ((seq(2 * counter + 1) << recordBits) | seq(2 * counter), if (flushAge) "age" else "token")
      else (BigInt(0), "-")
    val inReady = maybeFire && outReady
    val inFire = inValid && inReady
    val outFire = outValid && outReady
    val nCounter = if (inFire) 0 else if (outFire && !idleFlush) counter + 1 else counter
    if (pack) {
      val keepTail = (n % 2 == 1) && !pad
      val nIdle = if (idleFlush && outReady) 0
                  else if (inValid || !holdV || counter != 0) 0
                  else if (idle == idleLimit - 1) idle else idle + 1
      if (inFire) {
        val tail = if (n >= 1) seq(n - 1) else BigInt(0)
        val nAge = if (keepTail && holdV && k == 0) age + 1 else 0
        holdV = keepTail
        if (keepTail) hold = tail
        age = nAge
      } else if (idleFlush && outReady) {
        holdV = false
        age = 0
      }
      idle = nIdle
    }
    counter = nCounter
    (outValid, outBits, inReady, reason)
  }
}

class IFTBeatPackerSpec extends AnyFlatSpec with ChiselScalatestTester {
  val nSlots = 5
  val recordBits = 256
  val mask = (BigInt(1) << recordBits) - 1

  def runCase(pack: Boolean, seed: Int, ageLimit: Int, idleLimit: Int, nTokens: Int = 2000): Unit = {
    test(new IFTBeatPacker(nSlots, recordBits, pack, ageLimit, idleLimit)) { dut =>
      val rnd = new scala.util.Random(seed)
      var uid = 1L
      val tokens = (0 until nTokens).map { _ =>
        val v = if (rnd.nextDouble() < 0.3) Seq.fill(nSlots)(false) else Seq.fill(nSlots)(rnd.nextBoolean())
        val r = v.map { x =>
          if (x) { val rec = (BigInt(uid) << 8) | BigInt(Seq(1, 2, 3, 8)(rnd.nextInt(4))); uid += 1; rec }
          else BigInt(rnd.nextInt(1 << 30)) << 4                          // junk with class 0
        }
        (v, r)
      }
      val model = new IFTBeatPackerModel(nSlots, recordBits, pack, ageLimit, idleLimit)
      val beats = scala.collection.mutable.ArrayBuffer[(BigInt, String)]()
      var ti = 0
      var gap = 0
      var cycles = 0
      var drained = false
      dut.clock.setTimeout(0)
      while (!drained) {
        val inValid = ti < tokens.length && gap == 0
        val (v, r) = if (inValid) tokens(ti) else (Seq.fill(nSlots)(false), Seq.fill(nSlots)(BigInt(0)))
        val outReady = if (ti < tokens.length) rnd.nextDouble() < 0.7 else true
        dut.io.in.valid.poke(inValid.B)
        for (s <- 0 until nSlots) {
          dut.io.in.bits.valids(s).poke(v(s).B)
          dut.io.in.bits.records(s).poke(r(s).U(recordBits.W))
        }
        dut.io.out.ready.poke(outReady.B)
        val (mv, mb, mr, reason) = model.step(inValid, v, r, outReady)
        val dv = dut.io.out.valid.peek().litToBoolean
        val dr = dut.io.in.ready.peek().litToBoolean
        assert(dv == mv, s"cycle $cycles: out.valid dut=$dv model=$mv")
        assert(dr == mr, s"cycle $cycles: in.ready dut=$dr model=$mr")
        if (dv) {
          val db = dut.io.out.bits.peek().litValue
          assert(db == mb, s"cycle $cycles: out.bits differ")
          if (outReady) beats += ((db, reason))
        }
        if (!inValid && gap > 0) gap -= 1
        if (inValid && mr) { ti += 1; gap = Seq(0, 0, 0, 1, 2, 5, 17, 40)(rnd.nextInt(8)) }
        dut.clock.step()
        cycles += 1
        if (ti >= tokens.length && !model.holdV && model.counter == 0) drained = true
        assert(cycles < 50 * nTokens + 10000, "no progress")
      }
      // (2) order, no loss, no duplicate, no junk
      val expected = tokens.flatMap { case (v, r) => (v zip r).filter(_._1).map(_._2) }
      val got = beats.toSeq.flatMap { case (b, _) => Seq(b & mask, b >> recordBits) }.filter(_ != 0)
      assert(got.forall(x => (x & 0xF) != 0), "a junk (class 0) record reached the stream")
      assert(got == expected, s"record stream differs: got ${got.length}, expected ${expected.length}")
      // (3) padding rule
      if (pack) {
        val badPad = beats.count { case (b, why) => why == "token" && ((b & mask) == 0 || (b >> recordBits) == 0) }
        assert(badPad == 0, s"$badPad token beats carry a zero half")
      } else {
        val today = tokens.flatMap { case (v, r) =>
          val p = (v zip r).filter(_._1).map(_._2)
          val q = if (p.length % 2 == 1) p :+ BigInt(0) else p
          q.grouped(2).map(g => (g(1) << recordBits) | g(0))
        }
        assert(beats.map(_._1).toSeq == today, "packAcrossCycles = false: beats differ from today's")
      }
      println(f"[FT-11] pack=$pack%-5s seed=$seed age=$ageLimit idle=$idleLimit tokens=$nTokens records=${expected.length} beats=${beats.length} cycles=$cycles PASS")
    }
  }

  behavior of "IFTBeatPacker"
  it should "pack across cycles (ageLimit 16, idleLimit 256)" in { runCase(pack = true, seed = 1, ageLimit = 16, idleLimit = 256) }
  it should "pack across cycles with a short idle limit (idleLimit 16)" in { runCase(pack = true, seed = 2, ageLimit = 16, idleLimit = 16) }
  it should "pack across cycles with a short age limit (ageLimit 2, idleLimit 4)" in { runCase(pack = true, seed = 3, ageLimit = 2, idleLimit = 4) }
  it should "reproduce today's beats with packAcrossCycles = false" in { runCase(pack = false, seed = 4, ageLimit = 16, idleLimit = 256) }
}
